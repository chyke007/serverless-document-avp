"""
Lambda function for document upload initiation
Generates pre-signed S3 URLs for direct client uploads
"""

import json
import os
import sys
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import boto3
from botocore.exceptions import ClientError

# Add common module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'common'))
from common.retry_utils import retry_s3_operation, retry_dynamodb_operation, retry_avp_operation
from common.structured_logger import create_logger, EventType

# AWS X-Ray tracing
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

# Patch AWS SDK for X-Ray tracing
patch_all()

# Environment variables
S3_BUCKET_NAME = os.environ.get('S3_BUCKET_NAME')
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME')
POLICY_STORE_ID = os.environ.get('POLICY_STORE_ID')
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB
PRESIGNED_URL_EXPIRATION = 900  # 15 minutes in seconds

# AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
avp_client = boto3.client('verifiedpermissions')

# DynamoDB table
metadata_table = dynamodb.Table(DYNAMODB_TABLE_NAME)


class UploadError(Exception):
    """Custom exception for upload errors"""
    pass


@xray_recorder.capture('authorize_upload')
@retry_avp_operation
def authorize_upload(user_id: str, role: str, department: str, logger=None) -> bool:
    """
    Checks upload permission via Amazon Verified Permissions
    
    Implements retry logic with exponential backoff (2 retries).
    Fails closed if Policy Store is unavailable after retries.
    
    Args:
        user_id: User identifier
        role: User role (Admin, Manager, Editor, Viewer)
        department: User department
        logger: Structured logger instance (optional)
        
    Returns:
        True if authorized, False otherwise
    """
    try:
        # For document creation, we evaluate against a generic "create" action
        # All authenticated users can create documents (they become the owner)
        # The authorization is primarily role-based for this action
        
        # Construct the authorization request
        response = avp_client.is_authorized(
            policyStoreId=POLICY_STORE_ID,
            principal={
                'entityType': 'DocumentManagement::User',
                'entityId': user_id
            },
            action={
                'actionType': 'DocumentManagement::Action',
                'actionId': 'write'  # Use 'write' action for document creation
            },
            resource={
                'entityType': 'DocumentManagement::Document',
                'entityId': 'new-document'  # Placeholder for new document
            },
            entities={
                'entityList': [
                    {
                        'identifier': {
                            'entityType': 'DocumentManagement::User',
                            'entityId': user_id
                        },
                        'attributes': {
                            'userId': {'string': user_id},
                            'role': {'string': role},
                            'department': {'string': department}
                        }
                    },
                    {
                        'identifier': {
                            'entityType': 'DocumentManagement::Document',
                            'entityId': 'new-document'
                        },
                        'attributes': {
                            'documentId': {'string': 'new-document'},
                            'owner': {'string': user_id},
                            'department': {'string': department},
                            'sharedWith': {'set': []}
                        }
                    }
                ]
            }
        )
        
        decision = response.get('decision', 'DENY')
        
        # Log authorization decision
        if logger:
            logger.authorization_decision(
                action='upload',
                decision=decision,
                determining_policies=response.get('determiningPolicies', [])
            )
        else:
            print(json.dumps({
                'event_type': 'authorization_decision',
                'user_id': user_id,
                'action': 'upload',
                'decision': decision,
                'determining_policies': response.get('determiningPolicies', [])
            }))
        
        # Add X-Ray annotation
        xray_recorder.put_annotation('authorization_result', decision.lower())
        
        return decision == 'ALLOW'
        
    except ClientError as e:
        # Log error and fail closed (deny access)
        if logger:
            logger.error(
                EventType.AUTHORIZATION_ERROR,
                f"Authorization error for upload: {str(e)}",
                error=str(e)
            )
        else:
            print(json.dumps({
                'event_type': 'authorization_error',
                'error': str(e),
                'user_id': user_id
            }))
        xray_recorder.put_annotation('authorization_result', 'error')
        return False


@xray_recorder.capture('generate_presigned_upload_url')
@retry_s3_operation
def generate_presigned_upload_url(
    document_id: str,
    filename: str,
    content_type: str
) -> tuple[str, str]:
    """
    Generates pre-signed S3 URL for direct client upload
    
    Implements retry logic with exponential backoff (3 retries).
    
    Args:
        document_id: Unique document identifier
        filename: Original filename
        content_type: MIME type of the file
        
    Returns:
        Tuple of (presigned_url, s3_key)
        
    Raises:
        UploadError: If pre-signed URL generation fails
    """
    # Construct S3 key: documents/{document_id}/{filename}
    s3_key = f"documents/{document_id}/{filename}"
    
    try:
        # Generate pre-signed URL for PUT operation
        # Client will upload directly to S3 using this URL
        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': S3_BUCKET_NAME,
                'Key': s3_key,
                'ContentType': content_type,
                'ServerSideEncryption': 'AES256',  # Enforce encryption
                'Metadata': {
                    'document-id': document_id,
                    'original-filename': filename
                }
            },
            ExpiresIn=PRESIGNED_URL_EXPIRATION,  # 15 minutes
            HttpMethod='PUT'
        )
        
        print(json.dumps({
            'event_type': 'presigned_url_generated',
            'document_id': document_id,
            'filename': filename,
            's3_key': s3_key,
            'expiration_seconds': PRESIGNED_URL_EXPIRATION
        }))
        
        return presigned_url, s3_key
        
    except ClientError as e:
        error_msg = f"Pre-signed URL generation failed: {str(e)}"
        print(json.dumps({
            'event_type': 'presigned_url_error',
            'error': error_msg,
            'document_id': document_id
        }))
        raise UploadError(error_msg)


@xray_recorder.capture('create_pending_metadata')
@retry_dynamodb_operation
def create_pending_metadata(
    document_id: str,
    filename: str,
    owner: str,
    department: str,
    content_type: str,
    s3_key: str,
    metadata: Dict[str, Any]
) -> None:
    """
    Creates pending metadata entry in DynamoDB
    
    The metadata will be updated to "complete" status when the upload finishes.
    Implements retry logic with exponential backoff (3 retries).
    
    Args:
        document_id: Unique document identifier
        filename: Original filename
        owner: User ID of document owner
        department: Department of the owner
        content_type: MIME type of the file
        s3_key: S3 key where file will be stored
        metadata: Additional metadata (tags, etc.)
        
    Raises:
        UploadError: If DynamoDB operation fails
    """
    try:
        # Get current timestamp in ISO 8601 format
        timestamp = datetime.utcnow().isoformat() + 'Z'
        
        # Prepare DynamoDB item with "pending" status
        item = {
            'document_id': document_id,
            'filename': filename,
            'owner': owner,
            'department': department,
            'upload_initiated': timestamp,
            'upload_timestamp': timestamp,  # Will be updated on completion
            'status': 'pending',  # Status: pending, complete, failed
            'size_bytes': 0,  # Will be updated on completion
            'content_type': content_type,
            's3_key': s3_key,
            'shared_with': [],
            'tags': metadata.get('tags', []),
            'last_modified': timestamp,
            'version': 1
        }
        
        # Put item in DynamoDB
        metadata_table.put_item(Item=item)
        
        print(json.dumps({
            'event_type': 'pending_metadata_created',
            'document_id': document_id,
            'owner': owner,
            'filename': filename,
            'status': 'pending'
        }))
        
    except ClientError as e:
        error_msg = f"DynamoDB operation failed: {str(e)}"
        print(json.dumps({
            'event_type': 'dynamodb_error',
            'error': error_msg,
            'document_id': document_id
        }))
        raise UploadError(error_msg)


def log_operation(user_id: str, action: str, document_id: str, result: str, details: Optional[Dict] = None) -> None:
    """
    Logs operation to CloudWatch for audit trail
    
    Args:
        user_id: User identifier
        action: Action performed (upload_initiate, upload_complete, etc.)
        document_id: Document identifier
        result: Result of operation (success, failure, denied)
        details: Additional details to log
    """
    log_entry = {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'event_type': 'audit_log',
        'user_id': user_id,
        'action': action,
        'document_id': document_id,
        'result': result
    }
    
    if details:
        log_entry.update(details)
    
    print(json.dumps(log_entry))


@xray_recorder.capture('lambda_handler')
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for document upload initiation
    
    Validates authorization, generates pre-signed S3 URL, and creates pending metadata.
    Client will upload directly to S3 using the pre-signed URL.
    
    Args:
        event: API Gateway event containing request body and authorizer context
        context: Lambda context
        
    Returns:
        API Gateway response with pre-signed URL and document ID
    """
    # Wrap entire handler in try-except to catch any initialization errors
    try:
        # Create structured logger
        logger = create_logger('document-upload', context)
        
        # Extract request ID for correlation
        request_id = getattr(context, 'aws_request_id', None) or getattr(context, 'request_id', None)
        if request_id:
            xray_recorder.put_annotation('request_id', request_id)
        
        logger.info(EventType.LAMBDA_INVOCATION, "Upload Lambda invoked")
    except Exception as init_error:
        # If logger initialization fails, return error response
        print(json.dumps({
            'event_type': 'logger_init_error',
            'error': str(init_error),
            'error_type': type(init_error).__name__
        }))
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': 'Internal Server Error',
                'message': f'Logger initialization failed: {str(init_error)}'
            })
        }
    
    try:
        # Extract user identity from authorizer context
        # Handle both API Gateway v1 and v2 event formats
        request_context = event.get('requestContext', {})
        authorizer_context = request_context.get('authorizer', {}) or request_context.get('authorizer', {})
        
        # Try to get user info from authorizer context
        user_id = authorizer_context.get('userId') or authorizer_context.get('user_id')
        role = authorizer_context.get('role', 'Viewer')
        department = authorizer_context.get('department', '')
        email = authorizer_context.get('email', '')
        
        # Debug logging
        print(json.dumps({
            'event_type': 'authorizer_context_debug',
            'has_request_context': 'requestContext' in event,
            'has_authorizer': 'authorizer' in request_context,
            'authorizer_keys': list(authorizer_context.keys()) if authorizer_context else [],
            'user_id': user_id
        }))
        
        if not user_id:
            error_msg = 'Missing user identity from authorizer. Authorizer context: ' + json.dumps(authorizer_context)
            print(json.dumps({
                'event_type': 'authorizer_error',
                'error': error_msg,
                'request_context_keys': list(request_context.keys()) if request_context else []
            }))
            raise UploadError(error_msg)
        
        # Set user_id in logger
        logger.set_user_id(user_id)
        
        # Add X-Ray annotations
        xray_recorder.put_annotation('user_id', user_id)
        xray_recorder.put_annotation('role', role)
        
        # Parse request body
        body = json.loads(event.get('body', '{}'))
        filename = body.get('filename')
        content_type = body.get('content_type', 'application/octet-stream')
        max_file_size = body.get('max_file_size', MAX_FILE_SIZE_BYTES)
        metadata = body.get('metadata', {})
        
        # Validate required fields
        if not filename:
            raise UploadError('Missing required field: filename')
        
        # Validate max file size doesn't exceed system limit
        if max_file_size > MAX_FILE_SIZE_BYTES:
            raise UploadError(f'Requested file size {max_file_size} exceeds maximum {MAX_FILE_SIZE_BYTES} bytes')
        
        # Evaluate authorization via Verified Permissions
        if not authorize_upload(user_id, role, department, logger):
            logger.audit_log(
                action='upload_initiate',
                result='denied',
                document_id='N/A',
                filename=filename,
                email=email,
                reason='Authorization denied'
            )
            
            return {
                'statusCode': 403,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': 'Forbidden',
                    'message': 'You do not have permission to upload documents'
                })
            }
        
        # Generate unique document ID
        document_id = str(uuid.uuid4())
        xray_recorder.put_annotation('document_id', document_id)
        
        # Generate pre-signed S3 URL for client upload
        presigned_url, s3_key = generate_presigned_upload_url(
            document_id, filename, content_type
        )
        
        # Create pending metadata entry in DynamoDB
        create_pending_metadata(
            document_id=document_id,
            filename=filename,
            owner=user_id,
            department=department,
            content_type=content_type,
            s3_key=s3_key,
            metadata=metadata
        )
        
        # Log successful operation
        logger.audit_log(
            action='upload_initiate',
            result='success',
            document_id=document_id,
            filename=filename,
            email=email,
            max_file_size=max_file_size
        )
        
        # Calculate expiration timestamp (current time + expiration seconds)
        expires_at = (datetime.utcnow() + timedelta(seconds=PRESIGNED_URL_EXPIRATION)).isoformat() + 'Z'
        
        # Return success response with pre-signed URL
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'document_id': document_id,
                'presigned_url': presigned_url,
                's3_key': s3_key,
                'filename': filename,
                'content_type': content_type,  # Include content_type so frontend uses exact same value
                'expires_at': expires_at,
                'expires_in': PRESIGNED_URL_EXPIRATION,  # Keep for backward compatibility
                'upload_instructions': {
                    'method': 'PUT',
                    'headers': {
                        'Content-Type': content_type
                    },
                    'max_file_size': max_file_size
                }
            })
        }
        
    except UploadError as e:
        # Log operation failure
        logger.audit_log(
            action='upload_initiate',
            result='failure',
            document_id=document_id if 'document_id' in locals() else 'N/A',
            error=str(e)
        )
        
        return {
            'statusCode': 400,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': 'Bad Request',
                'message': str(e)
            })
        }
        
    except Exception as e:
        # Log unexpected error
        logger.error(
            EventType.UNEXPECTED_ERROR,
            f"Unexpected error in upload handler: {str(e)}",
            error=str(e)
        )
        
        logger.audit_log(
            action='upload_initiate',
            result='failure',
            document_id=document_id if 'document_id' in locals() else 'N/A',
            error=f'Internal error: {str(e)}'
        )
        
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': 'Internal Server Error',
                'message': 'An unexpected error occurred'
            })
        }

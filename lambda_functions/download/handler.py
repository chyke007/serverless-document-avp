"""
Lambda function for document download
Generates pre-signed S3 URLs for authorized document downloads
"""

import json
import os
import sys
from datetime import datetime
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
PRESIGNED_URL_EXPIRATION = 300  # 5 minutes in seconds

# AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
avp_client = boto3.client('verifiedpermissions')
cloudwatch = boto3.client('cloudwatch')

# DynamoDB table
metadata_table = dynamodb.Table(DYNAMODB_TABLE_NAME)


class DownloadError(Exception):
    """Custom exception for download errors"""
    pass


@xray_recorder.capture('get_document_metadata')
@retry_dynamodb_operation
def get_document_metadata(document_id: str) -> Dict[str, Any]:
    """
    Retrieves document metadata from DynamoDB
    
    Implements retry logic with exponential backoff (3 retries).
    
    Args:
        document_id: Document identifier
        
    Returns:
        Document metadata dictionary
        
    Raises:
        DownloadError: If document not found or DynamoDB operation fails
    """
    try:
        response = metadata_table.get_item(
            Key={'document_id': document_id}
        )
        
        if 'Item' not in response:
            raise DownloadError(f'Document not found: {document_id}')
        
        metadata = response['Item']
        
        # Check if document upload is complete
        if metadata.get('status') != 'complete':
            raise DownloadError(f'Document upload not complete: {document_id}')
        
        print(json.dumps({
            'event_type': 'metadata_retrieved',
            'document_id': document_id,
            'owner': metadata.get('owner'),
            'filename': metadata.get('filename')
        }))
        
        return metadata
        
    except ClientError as e:
        error_msg = f"DynamoDB operation failed: {str(e)}"
        print(json.dumps({
            'event_type': 'dynamodb_error',
            'error': error_msg,
            'document_id': document_id
        }))
        raise DownloadError(error_msg)


@xray_recorder.capture('authorize_download')
@retry_avp_operation
def authorize_download(
    user_id: str,
    role: str,
    department: str,
    document_id: str,
    document_owner: str,
    document_department: str,
    shared_with: list,
    logger=None
) -> bool:
    """
    Checks read permission via Amazon Verified Permissions
    
    Implements retry logic with exponential backoff (2 retries).
    Fails closed if Policy Store is unavailable after retries.
    
    Args:
        user_id: User identifier
        role: User role (Admin, Manager, Editor, Viewer)
        department: User department
        document_id: Document identifier
        document_owner: Document owner user ID
        document_department: Document department
        shared_with: List of user IDs document is shared with
        logger: Structured logger instance (optional)
        
    Returns:
        True if authorized, False otherwise
    """
    try:
        # Construct the authorization request
        response = avp_client.is_authorized(
            policyStoreId=POLICY_STORE_ID,
            principal={
                'entityType': 'DocumentManagement::User',
                'entityId': user_id
            },
            action={
                'actionType': 'DocumentManagement::Action',
                'actionId': 'read'
            },
            resource={
                'entityType': 'DocumentManagement::Document',
                'entityId': document_id
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
                            'entityId': document_id
                        },
                        'attributes': {
                            'documentId': {'string': document_id},
                            'owner': {'string': document_owner},
                            'department': {'string': document_department},
                            'sharedWith': {
                                'set': [{'string': uid} for uid in shared_with] if shared_with else []
                            }
                        }
                    }
                ]
            }
        )
        
        decision = response.get('decision', 'DENY')
        
        # Log authorization decision
        if logger:
            logger.authorization_decision(
                action='download',
                decision=decision,
                document_id=document_id,
                determining_policies=response.get('determiningPolicies', [])
            )
        else:
            print(json.dumps({
                'event_type': 'authorization_decision',
                'user_id': user_id,
                'action': 'download',
                'document_id': document_id,
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
                f"Authorization error for download: {str(e)}",
                error=str(e),
                document_id=document_id
            )
        else:
            print(json.dumps({
                'event_type': 'authorization_error',
                'error': str(e),
                'user_id': user_id,
                'document_id': document_id
            }))
        xray_recorder.put_annotation('authorization_result', 'error')
        return False


@xray_recorder.capture('generate_presigned_download_url')
@retry_s3_operation
def generate_presigned_download_url(s3_key: str, filename: str) -> str:
    """
    Generates pre-signed S3 URL for document download
    
    Implements retry logic with exponential backoff (3 retries).
    
    Args:
        s3_key: S3 object key
        filename: Original filename for Content-Disposition header
        
    Returns:
        Pre-signed URL string
        
    Raises:
        DownloadError: If pre-signed URL generation fails
    """
    try:
        # Generate pre-signed URL for GET operation
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': S3_BUCKET_NAME,
                'Key': s3_key,
                'ResponseContentDisposition': f'attachment; filename="{filename}"'
            },
            ExpiresIn=PRESIGNED_URL_EXPIRATION  # 5 minutes
        )
        
        print(json.dumps({
            'event_type': 'presigned_url_generated',
            's3_key': s3_key,
            'filename': filename,
            'expiration_seconds': PRESIGNED_URL_EXPIRATION
        }))
        
        return presigned_url
        
    except ClientError as e:
        error_msg = f"Pre-signed URL generation failed: {str(e)}"
        print(json.dumps({
            'event_type': 'presigned_url_error',
            'error': error_msg,
            's3_key': s3_key
        }))
        raise DownloadError(error_msg)


def emit_download_metric(document_id: str) -> None:
    """
    Emits custom CloudWatch metric for document download
    
    Args:
        document_id: Document identifier
    """
    try:
        cloudwatch.put_metric_data(
            Namespace='DocumentManagement',
            MetricData=[
                {
                    'MetricName': 'DocumentDownloads',
                    'Value': 1,
                    'Unit': 'Count',
                    'Timestamp': datetime.utcnow(),
                    'Dimensions': [
                        {
                            'Name': 'DocumentId',
                            'Value': document_id
                        }
                    ]
                }
            ]
        )
        
        print(json.dumps({
            'event_type': 'metric_emitted',
            'metric_name': 'DocumentDownloads',
            'document_id': document_id
        }))
        
    except ClientError as e:
        # Log error but don't fail the request
        print(json.dumps({
            'event_type': 'metric_error',
            'error': str(e),
            'document_id': document_id
        }))


def log_operation(
    user_id: str,
    action: str,
    document_id: str,
    result: str,
    details: Optional[Dict] = None
) -> None:
    """
    Logs operation to CloudWatch for audit trail
    
    Args:
        user_id: User identifier
        action: Action performed (download, etc.)
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
    Lambda handler for document download
    
    Validates authorization and generates pre-signed S3 URL for download.
    
    Args:
        event: API Gateway event containing path parameters and authorizer context
        context: Lambda context
        
    Returns:
        API Gateway response with pre-signed URL
    """
    # Create structured logger
    logger = create_logger('document-download', context)
    
    # Extract request ID for correlation
    request_id = getattr(context, 'aws_request_id', None) or getattr(context, 'request_id', None)
    xray_recorder.put_annotation('request_id', request_id)
    
    logger.info(EventType.LAMBDA_INVOCATION, "Download Lambda invoked")
    
    try:
        # Extract user identity from authorizer context
        authorizer_context = event.get('requestContext', {}).get('authorizer', {})
        user_id = authorizer_context.get('userId')
        role = authorizer_context.get('role', 'Viewer')
        department = authorizer_context.get('department', '')
        email = authorizer_context.get('email', '')
        
        if not user_id:
            raise DownloadError('Missing user identity from authorizer')
        
        # Set user_id in logger
        logger.set_user_id(user_id)
        
        # Add X-Ray annotations
        xray_recorder.put_annotation('user_id', user_id)
        xray_recorder.put_annotation('role', role)
        
        # Extract document ID from path parameters
        # API Gateway REST API maps {document_id} to 'document_id' in pathParameters
        path_parameters = event.get('pathParameters') or {}
        document_id = path_parameters.get('document_id') or path_parameters.get('id')  # Support both for compatibility
        
        if not document_id:
            raise DownloadError('Missing required path parameter: document_id')
        
        xray_recorder.put_annotation('document_id', document_id)
        
        # Retrieve document metadata from DynamoDB
        metadata = get_document_metadata(document_id)
        
        document_owner = metadata.get('owner')
        document_department = metadata.get('department', '')
        shared_with = metadata.get('shared_with', [])
        s3_key = metadata.get('s3_key')
        filename = metadata.get('filename')
        
        if not s3_key or not filename:
            raise DownloadError('Invalid document metadata: missing s3_key or filename')
        
        # Evaluate authorization via Verified Permissions
        if not authorize_download(
            user_id, role, department,
            document_id, document_owner, document_department, shared_with, logger
        ):
            logger.audit_log(
                action='download',
                result='denied',
                document_id=document_id,
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
                    'message': 'You do not have permission to download this document'
                })
            }
        
        # Generate pre-signed S3 URL
        presigned_url = generate_presigned_download_url(s3_key, filename)
        
        # Calculate expiration timestamp
        expires_at = datetime.utcnow().timestamp() + PRESIGNED_URL_EXPIRATION
        expires_at_iso = datetime.utcfromtimestamp(expires_at).isoformat() + 'Z'
        
        # Emit CloudWatch metric for download count
        emit_download_metric(document_id)
        
        # Log successful operation
        logger.audit_log(
            action='download',
            result='success',
            document_id=document_id,
            filename=filename,
            email=email,
            owner=document_owner
        )
        
        # Return success response with pre-signed URL
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'document_id': document_id,
                'filename': filename,
                'presigned_url': presigned_url,
                'expires_at': expires_at_iso,
                'expires_in': PRESIGNED_URL_EXPIRATION
            })
        }
        
    except DownloadError as e:
        # Log operation failure
        logger.audit_log(
            action='download',
            result='failure',
            document_id=document_id if 'document_id' in locals() else 'N/A',
            error=str(e)
        )
        
        # Return 404 for document not found, 400 for other errors
        status_code = 404 if 'not found' in str(e).lower() else 400
        
        return {
            'statusCode': status_code,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': 'Not Found' if status_code == 404 else 'Bad Request',
                'message': str(e)
            })
        }
        
    except Exception as e:
        # Log unexpected error
        logger.error(
            EventType.UNEXPECTED_ERROR,
            f"Unexpected error in download handler: {str(e)}",
            error=str(e)
        )
        
        logger.audit_log(
            action='download',
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

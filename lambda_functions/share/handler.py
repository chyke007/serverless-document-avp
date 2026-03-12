"""
Lambda function for document sharing
Grants document access permissions to other users
"""

import json
import os
import sys
from datetime import datetime
from typing import Dict, Any, Optional, List
import boto3
from botocore.exceptions import ClientError

# Add common module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'common'))
from common.retry_utils import retry_dynamodb_operation, retry_avp_operation
from common.structured_logger import create_logger, EventType

# AWS X-Ray tracing
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

# Patch AWS SDK for X-Ray tracing
patch_all()

# Environment variables
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME')
POLICY_STORE_ID = os.environ.get('POLICY_STORE_ID')

# AWS clients
dynamodb = boto3.resource('dynamodb')
avp_client = boto3.client('verifiedpermissions')

# DynamoDB table
metadata_table = dynamodb.Table(DYNAMODB_TABLE_NAME)


class ShareError(Exception):
    """Custom exception for share errors"""
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
        ShareError: If document not found or DynamoDB operation fails
    """
    try:
        response = metadata_table.get_item(
            Key={'document_id': document_id}
        )
        
        if 'Item' not in response:
            raise ShareError(f'Document not found: {document_id}')
        
        metadata = response['Item']
        
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
        raise ShareError(error_msg)


@xray_recorder.capture('authorize_share')
@retry_avp_operation
def authorize_share(
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
    Checks share permission via Amazon Verified Permissions
    
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
                'actionId': 'share'
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
                action='share',
                decision=decision,
                document_id=document_id,
                determining_policies=response.get('determiningPolicies', [])
            )
        else:
            print(json.dumps({
                'event_type': 'authorization_decision',
                'user_id': user_id,
                'action': 'share',
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
                f"Authorization error for share: {str(e)}",
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


@xray_recorder.capture('update_metadata_sharing')
@retry_dynamodb_operation
def update_metadata_sharing(
    document_id: str,
    target_user_id: str,
    permissions: List[str],
    current_shared_with: Optional[List[str]] = None
) -> None:
    """
    Updates metadata in DynamoDB with sharing information
    
    Adds the target user to the shared_with list in DynamoDB.
    The existing Cedar policies automatically grant access based on
    sharedWith.contains(principal.userId), so no individual policy creation is needed.
    
    Implements retry logic with exponential backoff (3 retries).
    
    Args:
        document_id: Document identifier
        target_user_id: User ID document is shared with (Cognito sub/UUID)
        permissions: List of granted permissions (for logging/audit purposes)
        current_shared_with: Current shared_with list (optional, will fetch if not provided)
        
    Raises:
        ShareError: If DynamoDB update fails
    """
    try:
        timestamp = datetime.utcnow().isoformat() + 'Z'
        
        # Get current shared_with list if not provided
        if current_shared_with is None:
            try:
                response = metadata_table.get_item(
                    Key={'document_id': document_id},
                    ProjectionExpression='shared_with'
                )
                current_shared_with = response.get('Item', {}).get('shared_with', [])
            except ClientError:
                # If get_item fails, start with empty list
                current_shared_with = []
        
        # Ensure it's a list (handle case where it might be a set or None)
        if current_shared_with is None:
            current_shared_with = []
        elif isinstance(current_shared_with, set):
            current_shared_with = list(current_shared_with)
        elif not isinstance(current_shared_with, list):
            # Convert to list if it's some other type
            current_shared_with = list(current_shared_with) if current_shared_with else []
        
        # Check if user is already in the list to avoid duplicates
        if target_user_id not in current_shared_with:
            current_shared_with.append(target_user_id)
        
        # Update the shared_with list with the new value
        # Use SET expression to replace the entire list (handles both list and set types)
        metadata_table.update_item(
            Key={'document_id': document_id},
            UpdateExpression='SET shared_with = :shared_with, last_modified = :timestamp',
            ExpressionAttributeValues={
                ':shared_with': current_shared_with,
                ':timestamp': timestamp
            }
        )
        
        print(json.dumps({
            'event_type': 'metadata_updated',
            'document_id': document_id,
            'target_user_id': target_user_id,
            'permissions': permissions,
            'message': 'User added to shared_with set. Existing Cedar policies will grant access.'
        }))
        
    except ClientError as e:
        error_msg = f"DynamoDB update failed: {str(e)}"
        print(json.dumps({
            'event_type': 'metadata_update_error',
            'error': error_msg,
            'document_id': document_id
        }))
        raise ShareError(error_msg)


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
        action: Action performed (share, etc.)
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
    Lambda handler for document sharing
    
    Validates authorization and grants document access to target user.
    
    Args:
        event: API Gateway event containing path parameters, request body, and authorizer context
        context: Lambda context
        
    Returns:
        API Gateway response with sharing confirmation
    """
    # Create structured logger
    logger = create_logger('document-share', context)
    
    # Extract request ID for correlation
    request_id = getattr(context, 'aws_request_id', None) or getattr(context, 'request_id', None)
    xray_recorder.put_annotation('request_id', request_id)
    
    logger.info(EventType.LAMBDA_INVOCATION, "Share Lambda invoked")
    
    try:
        # Extract user identity from authorizer context
        authorizer_context = event.get('requestContext', {}).get('authorizer', {})
        user_id = authorizer_context.get('userId')
        role = authorizer_context.get('role', 'Viewer')
        department = authorizer_context.get('department', '')
        email = authorizer_context.get('email', '')
        
        if not user_id:
            raise ShareError('Missing user identity from authorizer')
        
        # Set user_id in logger
        logger.set_user_id(user_id)
        
        # Add X-Ray annotations
        xray_recorder.put_annotation('user_id', user_id)
        xray_recorder.put_annotation('role', role)
        
        # Extract document ID from path parameters
        # API Gateway maps {document_id} to 'document_id' in pathParameters
        path_parameters = event.get('pathParameters') or {}
        document_id = path_parameters.get('document_id') or path_parameters.get('id')  # Support both for compatibility
        
        if not document_id:
            raise ShareError('Missing required path parameter: document_id')
        
        xray_recorder.put_annotation('document_id', document_id)
        
        # Parse request body
        body = json.loads(event.get('body', '{}'))
        target_user_id = body.get('user_id')
        permissions = body.get('permissions', [])
        
        # Validate required fields
        if not target_user_id:
            raise ShareError('Missing required field: user_id')
        
        if not permissions or not isinstance(permissions, list):
            raise ShareError('Missing or invalid field: permissions (must be non-empty list)')
        
        # Retrieve document metadata from DynamoDB
        metadata = get_document_metadata(document_id)
        
        document_owner = metadata.get('owner')
        document_department = metadata.get('department', '')
        shared_with = metadata.get('shared_with', [])
        filename = metadata.get('filename')
        
        # Evaluate authorization via Verified Permissions
        if not authorize_share(
            user_id, role, department,
            document_id, document_owner, document_department, shared_with, logger
        ):
            logger.audit_log(
                action='share',
                result='denied',
                document_id=document_id,
                filename=filename,
                email=email,
                target_user_id=target_user_id,
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
                    'message': 'You do not have permission to share this document'
                })
            }
        
        # Update metadata in DynamoDB with sharing information
        # The existing Cedar policies (editor_owned_shared_access, viewer_read_only_access)
        # automatically grant access based on sharedWith.contains(principal.userId)
        # No need to create individual policies - just update the shared_with list
        # Pass current shared_with to avoid extra DynamoDB call
        update_metadata_sharing(document_id, target_user_id, permissions, current_shared_with=shared_with)
        
        # Log successful operation
        logger.audit_log(
            action='share',
            result='success',
            document_id=document_id,
            filename=filename,
            email=email,
            owner=document_owner,
            target_user_id=target_user_id,
            permissions=permissions
        )
        
        # Return success response
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'message': 'Document shared successfully',
                'document_id': document_id,
                'filename': filename,
                'target_user_id': target_user_id,
                'permissions': permissions,
                'note': 'Access granted via existing Cedar policies based on sharedWith attribute'
            })
        }
        
    except ShareError as e:
        # Log operation failure
        logger.audit_log(
            action='share',
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
            f"Unexpected error in share handler: {str(e)}",
            error=str(e)
        )
        
        logger.audit_log(
            action='share',
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

"""
Lambda function for document list
Returns list of documents user has access to with pagination support
"""

import json
import os
import sys
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import boto3
from botocore.exceptions import ClientError
from decimal import Decimal

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
PAGE_SIZE = 50  # Default page size for pagination

# AWS clients
dynamodb = boto3.resource('dynamodb')
avp_client = boto3.client('verifiedpermissions')

# DynamoDB table
metadata_table = dynamodb.Table(DYNAMODB_TABLE_NAME)


class ListError(Exception):
    """Custom exception for list errors"""
    pass


def decimal_to_native(obj):
    """Convert DynamoDB types (Decimal, Set) to native Python types"""
    from decimal import Decimal
    
    if isinstance(obj, Decimal):
        if obj % 1 == 0:
            return int(obj)
        else:
            return float(obj)
    elif isinstance(obj, dict):
        return {k: decimal_to_native(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [decimal_to_native(item) for item in obj]
    elif isinstance(obj, set):
        # DynamoDB sets are returned as Python sets
        return [decimal_to_native(item) for item in obj]
    return obj


@xray_recorder.capture('list_documents_from_dynamodb')
@retry_dynamodb_operation
def list_documents_from_dynamodb(
    filters: Dict[str, Any],
    next_token: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Queries DynamoDB for documents with pagination
    
    Implements retry logic with exponential backoff (3 retries).
    
    Args:
        filters: Optional filters (owner, department, tags)
        next_token: Pagination token from previous request
        
    Returns:
        Tuple of (list of documents, next pagination token)
        
    Raises:
        ListError: If DynamoDB operation fails
    """
    try:
        # Build scan parameters
        scan_params: Dict[str, Any] = {
            'Limit': PAGE_SIZE,
        }
        
        # Add pagination token if provided
        if next_token:
            try:
                scan_params['ExclusiveStartKey'] = json.loads(next_token)
            except (json.JSONDecodeError, ValueError) as e:
                raise ListError(f'Invalid pagination token: {str(e)}')
        
        # Build filter expression for completed documents only.
        # IMPORTANT: Use condition objects (Attr) for DynamoDB Table resource API.
        from boto3.dynamodb.conditions import Attr

        filter_expr = Attr('status').eq('complete')

        # Add optional filters
        if filters.get('owner'):
            filter_expr = filter_expr & Attr('owner').eq(filters['owner'])

        if filters.get('department'):
            filter_expr = filter_expr & Attr('department').eq(filters['department'])

        scan_params['FilterExpression'] = filter_expr
        
        # Execute scan
        response = metadata_table.scan(**scan_params)
        
        documents = response.get('Items', [])
        
        # Convert Decimal types to native Python types
        documents = [decimal_to_native(doc) for doc in documents]
        
        # Get pagination token for next page
        last_evaluated_key = response.get('LastEvaluatedKey')
        next_page_token = json.dumps(last_evaluated_key) if last_evaluated_key else None
        
        print(json.dumps({
            'event_type': 'documents_retrieved',
            'count': len(documents),
            'has_more': bool(next_page_token)
        }))
        
        return documents, next_page_token
        
    except ClientError as e:
        error_msg = f"DynamoDB operation failed: {str(e)}"
        print(json.dumps({
            'event_type': 'dynamodb_error',
            'error': error_msg
        }))
        raise ListError(error_msg)


@xray_recorder.capture('authorize_document_read')
@retry_avp_operation
def authorize_document_read(
    user_id: str,
    role: str,
    department: str,
    document_id: str,
    document_owner: str,
    document_department: str,
    shared_with: List[str]
) -> bool:
    """
    Checks read permission for a single document via Amazon Verified Permissions
    
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
        
    Returns:
        True if authorized, False otherwise
    """
    try:
        # Normalize shared_with - ensure it's a list and handle None/empty cases
        if shared_with is None:
            shared_with = []
        elif isinstance(shared_with, (set, tuple)):
            shared_with = list(shared_with)
        elif not isinstance(shared_with, list):
            shared_with = [shared_with] if shared_with else []
        
        # Convert all items to strings to ensure consistency
        shared_with = [str(uid) for uid in shared_with if uid]
        
        # Log authorization request details for debugging
        print(json.dumps({
            'event_type': 'authorization_request',
            'user_id': user_id,
            'role': role,
            'document_id': document_id,
            'document_owner': document_owner,
            'shared_with': shared_with,
            'user_in_shared_with': user_id in shared_with,
            'user_is_owner': user_id == document_owner
        }))
        
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
        print(json.dumps({
            'event_type': 'authorization_decision',
            'decision': decision,
            'user_id': user_id,
            'document_id': document_id,
            'role': role,
            'user_is_owner': user_id == document_owner,
            'user_in_shared_with': user_id in shared_with,
            'shared_with_count': len(shared_with)
        }))
        
        return decision == 'ALLOW'
        
    except ClientError as e:
        # Log error and fail closed (deny access)
        print(json.dumps({
            'event_type': 'authorization_error',
            'error': str(e),
            'user_id': user_id,
            'document_id': document_id
        }))
        return False


@xray_recorder.capture('filter_authorized_documents')
def filter_authorized_documents(
    user_id: str,
    role: str,
    department: str,
    documents: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Filters documents based on read permissions
    
    Evaluates authorization for each document and returns only authorized ones.
    
    Args:
        user_id: User identifier
        role: User role
        department: User department
        documents: List of document metadata dictionaries
        
    Returns:
        Filtered list of authorized documents
    """
    authorized_documents = []
    denied_count = 0
    
    for doc in documents:
        document_id = doc.get('document_id')
        document_owner = doc.get('owner')
        document_department = doc.get('department', '')
        shared_with_raw = doc.get('shared_with', [])
        
        # Normalize shared_with - DynamoDB might return it as a set or list
        if shared_with_raw is None:
            shared_with = []
        elif isinstance(shared_with_raw, (set, tuple)):
            shared_with = list(shared_with_raw)
        elif isinstance(shared_with_raw, list):
            shared_with = shared_with_raw
        else:
            shared_with = [shared_with_raw] if shared_with_raw else []
        
        # Convert all items to strings for consistency
        shared_with = [str(uid) for uid in shared_with if uid]
        
        # Log document details for debugging denied documents
        user_is_owner = str(user_id) == str(document_owner)
        user_in_shared = str(user_id) in shared_with
        
        print(json.dumps({
            'event_type': 'document_authorization_check',
            'document_id': document_id,
            'user_id': user_id,
            'document_owner': document_owner,
            'user_is_owner': user_is_owner,
            'shared_with_raw': list(shared_with_raw) if isinstance(shared_with_raw, (set, list)) else shared_with_raw,
            'shared_with_normalized': shared_with,
            'user_in_shared_with': user_in_shared,
            'role': role
        }))
        
        # Evaluate authorization for this document
        if authorize_document_read(
            user_id, role, department,
            document_id, document_owner, document_department, shared_with
        ):
            authorized_documents.append(doc)
        else:
            denied_count += 1
            print(json.dumps({
                'event_type': 'document_authorization_denied',
                'document_id': document_id,
                'user_id': user_id,
                'reason': 'AVP returned DENY',
                'user_is_owner': user_is_owner,
                'user_in_shared_with': user_in_shared,
                'shared_with': shared_with
            }))
    
    print(json.dumps({
        'event_type': 'documents_filtered',
        'total_documents': len(documents),
        'authorized_documents': len(authorized_documents),
        'denied_documents': denied_count
    }))
    
    # Add X-Ray annotation
    xray_recorder.put_annotation('authorized_count', len(authorized_documents))
    xray_recorder.put_annotation('denied_count', denied_count)
    
    return authorized_documents


def log_operation(
    user_id: str,
    action: str,
    result: str,
    details: Optional[Dict] = None
) -> None:
    """
    Logs operation to CloudWatch for audit trail
    
    Args:
        user_id: User identifier
        action: Action performed (list, etc.)
        result: Result of operation (success, failure)
        details: Additional details to log
    """
    log_entry = {
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'event_type': 'audit_log',
        'user_id': user_id,
        'action': action,
        'result': result
    }
    
    if details:
        log_entry.update(details)
    
    print(json.dumps(log_entry))


@xray_recorder.capture('lambda_handler')
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for document list
    
    Returns list of documents user has access to with pagination support.
    
    Args:
        event: API Gateway event containing query parameters and authorizer context
        context: Lambda context
        
    Returns:
        API Gateway response with filtered document list
    """
    # Wrap entire handler in try-except to catch any initialization errors
    try:
        # Create structured logger
        logger = create_logger('document-list', context)
        
        # Extract request ID for correlation
        request_id = getattr(context, 'aws_request_id', None) or getattr(context, 'request_id', None)
        if request_id:
            xray_recorder.put_annotation('request_id', request_id)
        
        logger.info(EventType.LAMBDA_INVOCATION, "List Lambda invoked")
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
        request_context = event.get('requestContext', {})
        authorizer_context = request_context.get('authorizer', {})
        
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
            raise ListError(error_msg)
        
        # Set user_id in logger
        logger.set_user_id(user_id)
        
        # Add X-Ray annotations
        xray_recorder.put_annotation('user_id', user_id)
        xray_recorder.put_annotation('role', role)
        
        # Extract query parameters for filtering and pagination
        query_params = event.get('queryStringParameters') or {}
        filters = {}
        
        # Optional filters
        if query_params.get('owner'):
            filters['owner'] = query_params['owner']
        
        if query_params.get('department'):
            filters['department'] = query_params['department']
        
        if query_params.get('shared_with'):
            filters['shared_with'] = query_params['shared_with']
        
        # Pagination token
        next_token = query_params.get('nextToken')
        
        # Query DynamoDB for documents
        documents, next_page_token = list_documents_from_dynamodb(filters, next_token)
        
        # Filter documents based on authorization
        authorized_documents = filter_authorized_documents(
            user_id, role, department, documents
        )
        
        # Apply shared_with filter if specified (filter after authorization)
        if filters.get('shared_with'):
            shared_user_id = filters['shared_with']
            documents_before_filter = len(authorized_documents)
            
            # Filter to only documents shared with this user (exclude documents owned by the user)
            filtered_docs = []
            for doc in authorized_documents:
                doc_owner = doc.get('owner')
                doc_shared_with = doc.get('shared_with', [])
                
                # Convert to list if it's a set or other iterable
                if isinstance(doc_shared_with, (set, tuple)):
                    doc_shared_with = list(doc_shared_with)
                elif doc_shared_with is None:
                    doc_shared_with = []
                
                # Include if shared with user AND user is not the owner
                if doc_owner != shared_user_id and shared_user_id in doc_shared_with:
                    filtered_docs.append(doc)
            
            authorized_documents = filtered_docs
            
            # Log filtering result
            print(json.dumps({
                'event_type': 'shared_with_filter_applied',
                'shared_user_id': shared_user_id,
                'documents_before_filter': documents_before_filter,
                'documents_after_filter': len(filtered_docs)
            }))
        
        # Format response documents
        response_documents = []
        for doc in authorized_documents:
            response_documents.append({
                'document_id': doc.get('document_id'),
                'filename': doc.get('filename'),
                'owner': doc.get('owner'),
                'department': doc.get('department'),
                'upload_timestamp': doc.get('upload_timestamp'),
                'size_bytes': doc.get('size_bytes', 0),
                'content_type': doc.get('content_type'),
                'tags': doc.get('tags', []),
                'shared_with': doc.get('shared_with', []),
                'last_modified': doc.get('last_modified')
            })
        
        # Log successful operation
        logger.audit_log(
            action='list',
            result='success',
            email=email,
            document_count=len(response_documents),
            filters=filters,
            has_more=bool(next_page_token)
        )
        
        # Return success response with document list
        response_body = {
            'documents': response_documents,
            'count': len(response_documents)
        }
        
        if next_page_token:
            response_body['nextToken'] = next_page_token
        
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps(response_body)
        }
        
    except ListError as e:
        # Log operation failure
        logger.audit_log(
            action='list',
            result='failure',
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
        # Log unexpected error (handle case where logger might not be initialized)
        error_msg = str(e)
        error_type = type(e).__name__
        
        try:
            logger.error(
                EventType.UNEXPECTED_ERROR,
                f"Unexpected error in list handler: {error_msg}",
                error=error_msg
            )
            
            logger.audit_log(
                action='list',
                result='failure',
                error=f'Internal error: {error_msg}'
            )
        except:
            # If logger fails, just print
            print(json.dumps({
                'event_type': 'unexpected_error',
                'error': error_msg,
                'error_type': error_type
            }))
        
        return {
            'statusCode': 500,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'error': 'Internal Server Error',
                'message': f'An unexpected error occurred: {error_msg}',
                'error_type': error_type
            })
        }

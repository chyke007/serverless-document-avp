"""
Lambda function for retrieving audit logs
Queries CloudWatch Logs Insights to retrieve audit trail for documents
"""

import json
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
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
# Query all Lambda function log groups - audit logs are written by all functions
LOG_GROUP_PREFIX = os.environ.get('LOG_GROUP_PREFIX', '/aws/lambda/document-management')

# AWS clients
dynamodb = boto3.resource('dynamodb')
avp_client = boto3.client('verifiedpermissions')
logs_client = boto3.client('logs')

# DynamoDB table
metadata_table = dynamodb.Table(DYNAMODB_TABLE_NAME)


class AuditError(Exception):
    """Custom exception for audit errors"""
    pass


@xray_recorder.capture('get_document_metadata')
@retry_dynamodb_operation
def get_document_metadata(document_id: str) -> Dict[str, Any]:
    """
    Retrieve document metadata from DynamoDB
    
    Args:
        document_id: Document identifier
        
    Returns:
        Document metadata dictionary
        
    Raises:
        AuditError: If document not found or DynamoDB error
    """
    try:
        response = metadata_table.get_item(
            Key={'document_id': document_id}
        )
        
        if 'Item' not in response:
            raise AuditError(f'Document not found: {document_id}')
        
        return response['Item']
        
    except ClientError as e:
        error_msg = f"DynamoDB error: {str(e)}"
        print(json.dumps({
            'event_type': 'dynamodb_error',
            'error': error_msg,
            'document_id': document_id
        }))
        raise AuditError(error_msg)


@xray_recorder.capture('authorize_audit_access')
@retry_avp_operation
def authorize_audit_access(
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
    Checks if user has permission to view audit logs for a document
    
    Users can view audit logs if they have read access to the document.
    This uses the same authorization logic as document download.
    
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
        # Use "read" action since audit log access requires read permission
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
        
        # Log authorization decision with detailed context
        print(json.dumps({
            'event_type': 'authorization_decision',
            'decision': decision,
            'user_id': user_id,
            'document_id': document_id,
            'role': role,
            'department': department,
            'document_owner': document_owner,
            'document_department': document_department,
            'shared_with': shared_with if shared_with else []
        }))
        
        if logger:
            try:
                logger.info(
                    EventType.AUTHORIZATION_CHECK,
                    f"Audit access authorization: {decision}",
                    user_id=user_id,
                    document_id=document_id,
                    role=role,
                    decision=decision
                )
            except Exception as log_error:
                print(json.dumps({
                    'event_type': 'logger_error',
                    'error': str(log_error),
                    'error_type': type(log_error).__name__
                }))
        
        xray_recorder.put_annotation('authorization_result', decision.lower())
        
        if decision != 'ALLOW':
            # Log why access was denied
            print(json.dumps({
                'event_type': 'authorization_denied',
                'reason': 'AVP returned DENY decision',
                'user_id': user_id,
                'document_id': document_id,
                'user_is_owner': user_id == document_owner,
                'user_department': department,
                'document_department': document_department,
                'user_in_shared_with': user_id in (shared_with or [])
            }))
        
        return decision == 'ALLOW'
        
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        error_message = e.response.get('Error', {}).get('Message', str(e))
        error_msg = f"AVP authorization check failed: {error_code} - {error_message}"
        
        print(json.dumps({
            'event_type': 'authorization_error',
            'error': error_msg,
            'error_code': error_code,
            'error_message': error_message,
            'user_id': user_id,
            'document_id': document_id,
            'role': role,
            'document_owner': document_owner
        }))
        
        if logger:
            logger.error(
                EventType.AUTHORIZATION_ERROR,
                error_msg,
                user_id=user_id,
                document_id=document_id,
                error_code=error_code,
                error_message=error_message
            )
        
        xray_recorder.put_annotation('authorization_result', 'error')
        xray_recorder.put_metadata('error_code', error_code)
        return False
    
    except Exception as e:
        import traceback
        error_msg = f"Unexpected error during authorization check: {str(e)}"
        error_traceback = traceback.format_exc()
        
        print(json.dumps({
            'event_type': 'authorization_error',
            'error': error_msg,
            'error_type': type(e).__name__,
            'traceback': error_traceback,
            'user_id': user_id,
            'document_id': document_id
        }))
        
        if logger:
            logger.error(
                EventType.AUTHORIZATION_ERROR,
                error_msg,
                user_id=user_id,
                document_id=document_id,
                error_type=type(e).__name__
            )
        
        xray_recorder.put_annotation('authorization_result', 'error')
        return False


@xray_recorder.capture('query_audit_logs')
def query_audit_logs(document_id: str, log_group_prefix: str, limit: int = 100) -> List[Dict[str, Any]]:
    """
    Query CloudWatch Logs Insights for audit logs
    
    Args:
        document_id: Document identifier to filter audit logs
        log_group_prefix: CloudWatch Logs group name prefix (e.g., /aws/lambda/document-management)
        limit: Maximum number of logs to return
        
    Returns:
        List of audit log entries
    """
    try:
        # Find all log groups matching the prefix
        log_groups = []
        paginator = logs_client.get_paginator('describe_log_groups')
        
        for page in paginator.paginate(logGroupNamePrefix=log_group_prefix):
            for log_group in page.get('logGroups', []):
                log_groups.append(log_group['logGroupName'])
        
        if not log_groups:
            # Fallback to default log group name
            log_groups = [f"{log_group_prefix}-audit"]
        
        # Query CloudWatch Logs Insights
        # Look for audit_log events for the specific document
        # Query last 30 days of logs
        start_time = int((datetime.utcnow() - timedelta(days=30)).timestamp())
        end_time = int(datetime.utcnow().timestamp())
        
        query_string = f"""
        fields @timestamp, user_id, action, document_id, result, email, filename, owner, target_user_id, permissions, reason
        | filter event_type = "audit_log" and document_id = "{document_id}"
        | sort @timestamp desc
        | limit {limit}
        """
        
        # Start query across all log groups
        response = logs_client.start_query(
            logGroupNames=log_groups,
            startTime=start_time,
            endTime=end_time,
            queryString=query_string,
            limit=limit
        )
        
        query_id = response['queryId']
        
        # Wait for query to complete (poll every 0.5 seconds, max 10 seconds)
        import time
        max_wait = 10
        wait_time = 0.5
        elapsed = 0
        
        while elapsed < max_wait:
            response = logs_client.get_query_results(queryId=query_id)
            status = response.get('status', 'Unknown')
            
            if status == 'Complete':
                # Parse results
                results = []
                for result in response.get('results', []):
                    log_entry = {}
                    for field in result:
                        field_name = field.get('field', '')
                        field_value = field.get('value', '')
                        # Skip empty values but include all fields
                        if field_value:
                            log_entry[field_name] = field_value
                    
                    # Only include entries with valid document_id match
                    # CloudWatch Logs Insights might return partial matches, so verify
                    if log_entry.get('document_id') == document_id:
                        results.append(log_entry)
                
                return results
            
            elif status == 'Failed' or status == 'Cancelled':
                raise AuditError(f"CloudWatch Logs Insights query failed: {status}")
            
            time.sleep(wait_time)
            elapsed += wait_time
        
        # Query timed out
        raise AuditError("CloudWatch Logs Insights query timed out")
        
    except ClientError as e:
        error_msg = f"CloudWatch Logs error: {str(e)}"
        print(json.dumps({
            'event_type': 'cloudwatch_logs_error',
            'error': error_msg,
            'document_id': document_id
        }))
        raise AuditError(error_msg)


@xray_recorder.capture('lambda_handler')
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for audit log retrieval
    
    GET /documents/{document_id}/audit
    
    Returns audit logs for a specific document.
    User must have read access to the document.
    
    Args:
        event: API Gateway event
        context: Lambda context
        
    Returns:
        API Gateway response with audit logs
    """
    # Create structured logger
    logger = create_logger('audit', context)
    
    # Log the incoming event (without sensitive data)
    logger.info(
        EventType.LAMBDA_INVOCATION,
        "Audit Lambda invoked",
        path=event.get('path'),
        method=event.get('httpMethod')
    )
    
    try:
        # Extract user context from authorizer
        authorizer_context = event.get('requestContext', {}).get('authorizer', {})
        user_id = authorizer_context.get('userId')
        role = authorizer_context.get('role', 'Viewer')
        department = authorizer_context.get('department', '')
        email = authorizer_context.get('email', '')
        
        if not user_id:
            raise AuditError('Missing user identity from authorizer')
        
        # Set user_id in logger
        logger.set_user_id(user_id)
        
        # Add X-Ray annotations
        xray_recorder.put_annotation('user_id', user_id)
        xray_recorder.put_annotation('role', role)
        
        # Extract document ID from path parameters
        path_parameters = event.get('pathParameters') or {}
        document_id = path_parameters.get('document_id') or path_parameters.get('id')
        
        if not document_id:
            raise AuditError('Missing required path parameter: document_id')
        
        xray_recorder.put_annotation('document_id', document_id)
        
        # Get document metadata
        metadata = get_document_metadata(document_id)
        
        document_owner = metadata.get('owner')
        document_department = metadata.get('department', '')
        shared_with = metadata.get('shared_with', [])
        filename = metadata.get('filename', 'Unknown')
        
        # Check authorization
        if not authorize_audit_access(
            user_id, role, department,
            document_id, document_owner, document_department, shared_with, logger
        ):
            logger.audit_log(
                action='audit_view',
                result='denied',
                document_id=document_id,
                filename=filename,
                email=email,
                reason='Insufficient permissions to view audit logs'
            )
            
            return {
                'statusCode': 403,
                'headers': {
                    'Content-Type': 'application/json',
                    'Access-Control-Allow-Origin': '*'
                },
                'body': json.dumps({
                    'error': 'Forbidden',
                    'message': 'You do not have permission to view audit logs for this document'
                })
            }
        
        # Query audit logs from CloudWatch Logs
        audit_logs = query_audit_logs(document_id, LOG_GROUP_PREFIX)
        
        # Format audit logs for response
        formatted_logs = []
        for log in audit_logs:
            formatted_logs.append({
                'timestamp': log.get('@timestamp', log.get('timestamp', '')),
                'user_id': log.get('user_id', 'Unknown'),
                'action': log.get('action', 'unknown'),
                'document_id': log.get('document_id', document_id),
                'result': log.get('result', 'unknown'),
                'email': log.get('email', ''),
                'filename': log.get('filename', filename),
                'owner': log.get('owner', ''),
                'target_user_id': log.get('target_user_id', ''),
                'permissions': log.get('permissions', ''),
                'reason': log.get('reason', ''),
            })
        
        # Log successful operation
        logger.audit_log(
            action='audit_view',
            result='success',
            document_id=document_id,
            filename=filename,
            email=email,
            log_count=len(formatted_logs)
        )
        
        # Return success response
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Access-Control-Allow-Origin': '*'
            },
            'body': json.dumps({
                'audit_logs': formatted_logs,
                'document_id': document_id,
                'filename': filename,
                'count': len(formatted_logs)
            })
        }
        
    except AuditError as e:
        # Log operation failure
        logger.audit_log(
            action='audit_view',
            result='failure',
            document_id=event.get('pathParameters', {}).get('document_id', 'Unknown'),
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
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(
            EventType.ERROR,
            error_msg,
            error_type=type(e).__name__
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

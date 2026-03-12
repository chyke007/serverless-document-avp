"""
Lambda function for cleaning up abandoned uploads
Triggered by CloudWatch Events (daily schedule)
"""

import json
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, Any, List
import boto3
from botocore.exceptions import ClientError

# Add common module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'common'))
from common.retry_utils import retry_s3_operation, retry_dynamodb_operation

# AWS X-Ray tracing
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

# Patch AWS SDK for X-Ray tracing
patch_all()

# Environment variables
S3_BUCKET_NAME = os.environ.get('S3_BUCKET_NAME')
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME')
ABANDONED_THRESHOLD_HOURS = int(os.environ.get('ABANDONED_THRESHOLD_HOURS', '24'))

# AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')

# DynamoDB table
metadata_table = dynamodb.Table(DYNAMODB_TABLE_NAME)


@xray_recorder.capture('find_abandoned_uploads')
@retry_dynamodb_operation
def find_abandoned_uploads() -> List[Dict[str, Any]]:
    """
    Queries DynamoDB for pending uploads older than threshold
    
    Implements retry logic with exponential backoff (3 retries).
    
    Returns:
        List of abandoned upload metadata items
    """
    abandoned_items = []
    
    try:
        # Calculate threshold timestamp (current time - 24 hours)
        threshold_time = datetime.utcnow() - timedelta(hours=ABANDONED_THRESHOLD_HOURS)
        threshold_timestamp = threshold_time.isoformat() + 'Z'
        
        print(json.dumps({
            'event_type': 'scanning_for_abandoned_uploads',
            'threshold_timestamp': threshold_timestamp,
            'threshold_hours': ABANDONED_THRESHOLD_HOURS
        }))
        
        # Scan DynamoDB for items with status='pending' and old upload_initiated timestamp
        # Note: In production, consider using a GSI on status + upload_initiated for better performance
        response = metadata_table.scan(
            FilterExpression='#status = :pending AND upload_initiated < :threshold',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':pending': 'pending',
                ':threshold': threshold_timestamp
            }
        )
        
        abandoned_items.extend(response.get('Items', []))
        
        # Handle pagination if there are more items
        while 'LastEvaluatedKey' in response:
            response = metadata_table.scan(
                FilterExpression='#status = :pending AND upload_initiated < :threshold',
                ExpressionAttributeNames={
                    '#status': 'status'
                },
                ExpressionAttributeValues={
                    ':pending': 'pending',
                    ':threshold': threshold_timestamp
                },
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            abandoned_items.extend(response.get('Items', []))
        
        print(json.dumps({
            'event_type': 'abandoned_uploads_found',
            'count': len(abandoned_items)
        }))
        
        return abandoned_items
        
    except ClientError as e:
        error_msg = f"DynamoDB scan failed: {str(e)}"
        print(json.dumps({
            'event_type': 'dynamodb_scan_error',
            'error': error_msg
        }))
        raise


@xray_recorder.capture('delete_s3_object')
@retry_s3_operation
def delete_s3_object(s3_key: str) -> bool:
    """
    Deletes orphaned S3 object
    
    Implements retry logic with exponential backoff (3 retries).
    
    Args:
        s3_key: S3 object key to delete
        
    Returns:
        True if deleted successfully, False otherwise
    """
    try:
        s3_client.delete_object(
            Bucket=S3_BUCKET_NAME,
            Key=s3_key
        )
        
        print(json.dumps({
            'event_type': 's3_object_deleted',
            's3_key': s3_key
        }))
        
        return True
        
    except ClientError as e:
        # Check if object doesn't exist (already deleted or never uploaded)
        if e.response['Error']['Code'] == 'NoSuchKey':
            print(json.dumps({
                'event_type': 's3_object_not_found',
                's3_key': s3_key,
                'reason': 'Object does not exist'
            }))
            return True  # Consider this a success - object is gone
        else:
            error_msg = f"S3 delete failed: {str(e)}"
            print(json.dumps({
                'event_type': 's3_delete_error',
                'error': error_msg,
                's3_key': s3_key
            }))
            return False


@xray_recorder.capture('delete_metadata')
@retry_dynamodb_operation
def delete_metadata(document_id: str) -> bool:
    """
    Deletes pending metadata entry from DynamoDB
    
    Implements retry logic with exponential backoff (3 retries).
    
    Args:
        document_id: Document identifier
        
    Returns:
        True if deleted successfully, False otherwise
    """
    try:
        metadata_table.delete_item(
            Key={'document_id': document_id},
            ConditionExpression='attribute_exists(document_id)'
        )
        
        print(json.dumps({
            'event_type': 'metadata_deleted',
            'document_id': document_id
        }))
        
        return True
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            print(json.dumps({
                'event_type': 'metadata_not_found',
                'document_id': document_id,
                'reason': 'Metadata does not exist'
            }))
            return True  # Consider this a success - metadata is gone
        else:
            error_msg = f"DynamoDB delete failed: {str(e)}"
            print(json.dumps({
                'event_type': 'dynamodb_delete_error',
                'error': error_msg,
                'document_id': document_id
            }))
            return False


@xray_recorder.capture('cleanup_abandoned_upload')
def cleanup_abandoned_upload(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Cleans up a single abandoned upload
    
    Args:
        item: DynamoDB metadata item
        
    Returns:
        Cleanup result with status and details
    """
    document_id = item.get('document_id')
    s3_key = item.get('s3_key')
    filename = item.get('filename', 'unknown')
    owner = item.get('owner', 'unknown')
    upload_initiated = item.get('upload_initiated', 'unknown')
    
    print(json.dumps({
        'event_type': 'cleaning_abandoned_upload',
        'document_id': document_id,
        'filename': filename,
        'owner': owner,
        'upload_initiated': upload_initiated
    }))
    
    # Add X-Ray annotation
    xray_recorder.put_annotation('document_id', document_id)
    
    result = {
        'document_id': document_id,
        'filename': filename,
        'owner': owner,
        's3_deleted': False,
        'metadata_deleted': False,
        'success': False
    }
    
    # Delete S3 object (if it exists)
    if s3_key:
        result['s3_deleted'] = delete_s3_object(s3_key)
    else:
        # No S3 key means object was never uploaded
        result['s3_deleted'] = True
        print(json.dumps({
            'event_type': 's3_key_missing',
            'document_id': document_id,
            'reason': 'No S3 key in metadata'
        }))
    
    # Delete metadata from DynamoDB
    result['metadata_deleted'] = delete_metadata(document_id)
    
    # Overall success if both operations succeeded
    result['success'] = result['s3_deleted'] and result['metadata_deleted']
    
    if result['success']:
        print(json.dumps({
            'event_type': 'abandoned_upload_cleaned',
            'document_id': document_id,
            'filename': filename
        }))
    else:
        print(json.dumps({
            'event_type': 'abandoned_upload_cleanup_failed',
            'document_id': document_id,
            'filename': filename,
            's3_deleted': result['s3_deleted'],
            'metadata_deleted': result['metadata_deleted']
        }))
    
    return result


@xray_recorder.capture('lambda_handler')
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for cleanup of abandoned uploads
    
    Triggered by CloudWatch Events on a daily schedule.
    Finds pending uploads older than 24 hours and cleans them up.
    
    Args:
        event: CloudWatch Events scheduled event
        context: Lambda context
        
    Returns:
        Summary of cleanup operations
    """
    print(json.dumps({
        'event_type': 'cleanup_started',
        'request_id': getattr(context, 'aws_request_id', None) or getattr(context, 'request_id', None),
        'threshold_hours': ABANDONED_THRESHOLD_HOURS
    }))
    
    try:
        # Find abandoned uploads
        abandoned_items = find_abandoned_uploads()
        
        if not abandoned_items:
            print(json.dumps({
                'event_type': 'cleanup_complete',
                'abandoned_count': 0,
                'message': 'No abandoned uploads found'
            }))
            
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'No abandoned uploads found',
                    'abandoned_count': 0,
                    'cleaned_count': 0
                })
            }
        
        # Clean up each abandoned upload
        cleanup_results = []
        for item in abandoned_items:
            result = cleanup_abandoned_upload(item)
            cleanup_results.append(result)
        
        # Calculate summary statistics
        total_abandoned = len(cleanup_results)
        successful_cleanups = sum(1 for r in cleanup_results if r['success'])
        failed_cleanups = total_abandoned - successful_cleanups
        
        print(json.dumps({
            'event_type': 'cleanup_complete',
            'total_abandoned': total_abandoned,
            'successful_cleanups': successful_cleanups,
            'failed_cleanups': failed_cleanups
        }))
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Cleanup completed',
                'total_abandoned': total_abandoned,
                'successful_cleanups': successful_cleanups,
                'failed_cleanups': failed_cleanups,
                'results': cleanup_results
            })
        }
        
    except Exception as e:
        error_msg = f"Cleanup failed: {str(e)}"
        print(json.dumps({
            'event_type': 'cleanup_error',
            'error': error_msg,
            'request_id': getattr(context, 'aws_request_id', None) or getattr(context, 'request_id', None)
        }))
        
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Internal Server Error',
                'message': error_msg
            })
        }

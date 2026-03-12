"""
Lambda function for upload completion
Triggered by EventBridge when S3 ObjectCreated events occur

This function is invoked via EventBridge when a file is uploaded to S3.
It updates the document metadata status from 'pending' to 'complete' and
records the actual file size.
"""

import json
import os
import sys
import urllib.parse
from datetime import datetime
from typing import Dict, Any
import boto3
from botocore.exceptions import ClientError

# Add common module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'common'))
from common.retry_utils import retry_dynamodb_operation

# AWS X-Ray tracing
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

# Patch AWS SDK for X-Ray tracing
patch_all()

# Environment variables
DYNAMODB_TABLE_NAME = os.environ.get('DYNAMODB_TABLE_NAME')

# AWS clients
s3_client = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
cloudwatch = boto3.client('cloudwatch')

# DynamoDB table
metadata_table = dynamodb.Table(DYNAMODB_TABLE_NAME)


@xray_recorder.capture('extract_document_id')
def extract_document_id(s3_key: str) -> str:
    """
    Extracts document ID from S3 key
    
    S3 key format: documents/{document_id}/{filename}
    
    Args:
        s3_key: S3 object key
        
    Returns:
        Document ID
    """
    parts = s3_key.split('/')
    if len(parts) >= 2 and parts[0] == 'documents':
        return parts[1]
    raise ValueError(f'Invalid S3 key format: {s3_key}')


@xray_recorder.capture('update_metadata_complete')
@retry_dynamodb_operation
def update_metadata_complete(document_id: str, size_bytes: int) -> None:
    """
    Updates metadata status from pending to complete
    
    Implements retry logic with exponential backoff (3 retries).
    
    Args:
        document_id: Document identifier
        size_bytes: Actual file size in bytes
        
    Raises:
        Exception: If DynamoDB update fails
    """
    try:
        timestamp = datetime.utcnow().isoformat() + 'Z'
        
        # Update metadata with actual file size and completion timestamp
        response = metadata_table.update_item(
            Key={'document_id': document_id},
            UpdateExpression='SET #status = :status, size_bytes = :size, upload_timestamp = :timestamp, last_modified = :timestamp',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':status': 'complete',
                ':size': size_bytes,
                ':timestamp': timestamp,
                ':pending': 'pending'
            },
            ConditionExpression='attribute_exists(document_id) AND #status = :pending',
            ReturnValues='ALL_NEW'
        )
        
        print(json.dumps({
            'event_type': 'metadata_updated',
            'document_id': document_id,
            'status': 'complete',
            'size_bytes': size_bytes
        }))
        
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            # Metadata doesn't exist or is not in pending status
            print(json.dumps({
                'event_type': 'metadata_update_skipped',
                'document_id': document_id,
                'reason': 'Metadata not found or not in pending status'
            }))
        else:
            error_msg = f"DynamoDB update failed: {str(e)}"
            print(json.dumps({
                'event_type': 'dynamodb_error',
                'error': error_msg,
                'document_id': document_id
            }))
            raise


def emit_metric(metric_name: str, value: float, unit: str = 'Count') -> None:
    """
    Emits custom CloudWatch metric
    
    Args:
        metric_name: Name of the metric
        value: Metric value
        unit: Metric unit (Count, Milliseconds, etc.)
    """
    try:
        cloudwatch.put_metric_data(
            Namespace='DocumentManagement',
            MetricData=[
                {
                    'MetricName': metric_name,
                    'Value': value,
                    'Unit': unit,
                    'Timestamp': datetime.utcnow()
                }
            ]
        )
    except ClientError as e:
        # Log error but don't fail the request
        print(json.dumps({
            'event_type': 'metric_emission_error',
            'error': str(e),
            'metric_name': metric_name
        }))


@xray_recorder.capture('lambda_handler')
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for S3 upload events
    
    Supports both EventBridge and direct S3 event notifications.
    Triggered when a file is uploaded to S3. Updates metadata status to complete.
    
    Args:
        event: EventBridge event or S3 event notification
        context: Lambda context
        
    Returns:
        Success response
    """
    print(json.dumps({
        'event_type': 'upload_complete_triggered',
        'request_id': getattr(context, 'aws_request_id', None) or getattr(context, 'request_id', None)
    }))
    
    try:
        # Determine event type and extract S3 details
        if 'detail' in event and 'source' in event and event['source'] == 'aws.s3':
            # EventBridge event from S3
            print(json.dumps({
                'event_type': 'eventbridge_event_received',
                'detail_type': event.get('detail-type', '')
            }))
            
            # Process EventBridge S3 event
            detail = event['detail']
            bucket_name = detail.get('bucket', {}).get('name', '')
            s3_key = urllib.parse.unquote_plus(detail.get('object', {}).get('key', ''))
            size_bytes = detail.get('object', {}).get('size', 0)
            event_name = f"ObjectCreated:{detail.get('reason', 'Unknown')}"
            
            print(json.dumps({
                'event_type': 's3_event_received',
                'source': 'eventbridge',
                'bucket': bucket_name,
                's3_key': s3_key,
                'size_bytes': size_bytes,
                'event_name': event_name
            }))
            
            # Extract document ID from S3 key
            try:
                document_id = extract_document_id(s3_key)
            except ValueError as e:
                print(json.dumps({
                    'event_type': 'invalid_s3_key',
                    'error': str(e),
                    's3_key': s3_key
                }))
                return {
                    'statusCode': 400,
                    'body': json.dumps({'error': str(e)})
                }
            
            # Add X-Ray annotation
            xray_recorder.put_annotation('document_id', document_id)
            
            # Update metadata status to complete
            update_metadata_complete(document_id, size_bytes)
            
            # Emit custom CloudWatch metric for upload count
            emit_metric('DocumentUploads', 1)
            
            print(json.dumps({
                'event_type': 'upload_complete_success',
                'document_id': document_id,
                'size_bytes': size_bytes
            }))
            
        elif 'Records' in event:
            # Direct S3 event notification (legacy support)
            print(json.dumps({
                'event_type': 'direct_s3_event_received',
                'record_count': len(event.get('Records', []))
            }))
            
            # Process each S3 record
            for record in event.get('Records', []):
                # Extract S3 event details
                event_name = record.get('eventName', '')
                
                # Only process PUT events (ObjectCreated:Put, ObjectCreated:CompleteMultipartUpload)
                if not event_name.startswith('ObjectCreated:'):
                    print(json.dumps({
                        'event_type': 'event_skipped',
                        'event_name': event_name,
                        'reason': 'Not an ObjectCreated event'
                    }))
                    continue
                
                s3_info = record.get('s3', {})
                bucket_name = s3_info.get('bucket', {}).get('name', '')
                s3_key = urllib.parse.unquote_plus(s3_info.get('object', {}).get('key', ''))
                size_bytes = s3_info.get('object', {}).get('size', 0)
                
                print(json.dumps({
                    'event_type': 's3_event_received',
                    'source': 'direct_s3',
                    'bucket': bucket_name,
                    's3_key': s3_key,
                    'size_bytes': size_bytes,
                    'event_name': event_name
                }))
                
                # Extract document ID from S3 key
                try:
                    document_id = extract_document_id(s3_key)
                except ValueError as e:
                    print(json.dumps({
                        'event_type': 'invalid_s3_key',
                        'error': str(e),
                        's3_key': s3_key
                    }))
                    continue
                
                # Add X-Ray annotation
                xray_recorder.put_annotation('document_id', document_id)
                
                # Update metadata status to complete
                update_metadata_complete(document_id, size_bytes)
                
                # Emit custom CloudWatch metric for upload count
                emit_metric('DocumentUploads', 1)
                
                print(json.dumps({
                    'event_type': 'upload_complete_success',
                    'document_id': document_id,
                    'size_bytes': size_bytes
                }))
        else:
            # Unknown event format
            print(json.dumps({
                'event_type': 'unknown_event_format',
                'event_keys': list(event.keys())
            }))
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Unknown event format'})
            }
        
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Upload completion processed successfully'})
        }
        
    except Exception as e:
        print(json.dumps({
            'event_type': 'upload_complete_error',
            'error': str(e),
            'request_id': getattr(context, 'aws_request_id', None) or getattr(context, 'request_id', None)
        }))
        
        # Don't raise exception - we don't want EventBridge/S3 to retry
        # Failed updates will be cleaned up by the cleanup Lambda
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }

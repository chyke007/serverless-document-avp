# Document Management System - Lambda Functions

Python Lambda functions for document operations.

## Structure

Lambda functions are organized by functionality:
- `authorizer/` - Lambda authorizer for API Gateway (JWT validation)
- `upload/` - Document upload handler (generates presigned URLs)
- `upload_complete/` - Upload completion handler (triggered by S3 via EventBridge)
- `download/` - Document download handler (generates presigned URLs)
- `list/` - Document list handler (with authorization filtering)
- `delete/` - Document delete handler
- `share/` - Document share handler
- `cleanup/` - Cleanup handler for abandoned uploads (scheduled daily)
- `common/` - Shared utilities (retry logic, structured logging)

## Event Triggers

### API Gateway (via Lambda Authorizer)
- `authorizer` - Validates JWT tokens for all API requests
- `upload` - POST /upload
- `download` - GET /download/{document_id}
- `list` - GET /list
- `delete` - DELETE /delete/{document_id}
- `share` - POST /share/{document_id}

### EventBridge (S3 Events)
- `upload_complete` - Triggered when objects are created in S3 bucket

### EventBridge (Scheduled)
- `cleanup` - Runs daily at 2 AM UTC to clean up abandoned uploads

## Dependencies

Each Lambda function has its own `requirements.txt` for dependencies.

Common dependencies include:
- `boto3` - AWS SDK
- `aws-xray-sdk` - X-Ray tracing
- `python-jose` - JWT handling (authorizer only)

## Testing

Property-based tests using Hypothesis are located in each function's directory as `test_handler.py`.

## Common Utilities

The `common/` directory contains shared utilities:
- `retry_utils.py` - Exponential backoff retry logic for DynamoDB
- `structured_logger.py` - Structured JSON logging for CloudWatch

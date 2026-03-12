# Common Lambda Utilities

This directory contains shared utilities used across all Lambda functions in the Document Management System.

## Retry Utilities

The `retry_utils.py` module provides retry logic with exponential backoff for AWS service operations.

### Features

- **Exponential Backoff**: Automatically retries failed operations with increasing delays
- **Jitter**: Adds randomization to prevent thundering herd problems
- **Service-Specific Configuration**: Different retry policies for S3, DynamoDB, and Verified Permissions
- **Fail-Closed Authorization**: Denies access when Policy Store is unavailable
- **X-Ray Integration**: Works seamlessly with AWS X-Ray tracing

### Requirements

Implements requirements:
- **11.2**: S3 operations retry on failure with exponential backoff (3 retries)
- **11.3**: DynamoDB operations retry on throttling with exponential backoff (3 retries)
- **11.4**: Verified Permissions operations retry with fail-closed behavior (2 retries)

### Usage

#### Decorators

The simplest way to add retry logic is using decorators:

```python
from retry_utils import retry_s3_operation, retry_dynamodb_operation, retry_avp_operation

@retry_s3_operation
def upload_to_s3(bucket, key, data):
    s3_client.put_object(Bucket=bucket, Key=key, Body=data)

@retry_dynamodb_operation
def save_metadata(table, item):
    table.put_item(Item=item)

@retry_avp_operation
def authorize_action(user_id, action, resource):
    response = avp_client.is_authorized(...)
    return response['decision'] == 'ALLOW'
```

#### Manual Retry

For more control, use the `execute_with_retry` function:

```python
from retry_utils import execute_with_retry

def my_operation():
    return s3_client.get_object(Bucket='my-bucket', Key='my-key')

result = execute_with_retry(
    operation=my_operation,
    operation_type='s3',
    max_retries=5  # Override default
)
```

### Configuration

Default retry configurations:

| Service | Max Retries | Base Delay | Max Delay | Retryable Errors |
|---------|-------------|------------|-----------|------------------|
| S3 | 3 | 100ms | 2s | ServiceUnavailable, RequestTimeout, SlowDown, InternalError, 5xx |
| DynamoDB | 3 | 100ms | 2s | ProvisionedThroughputExceededException, ThrottlingException, RequestLimitExceeded, InternalServerError, ServiceUnavailable |
| Verified Permissions | 2 | 100ms | 1s | ThrottlingException, ServiceUnavailable, InternalServerError, RequestTimeout |

### Exponential Backoff Algorithm

The retry delay is calculated as:

```
delay = min(base_delay * 2^attempt, max_delay) ± 25% jitter
```

Example delays for S3 operations:
- Attempt 1: ~100ms (75-125ms with jitter)
- Attempt 2: ~200ms (150-250ms with jitter)
- Attempt 3: ~400ms (300-500ms with jitter)

### Fail-Closed Behavior

For authorization operations using `@retry_avp_operation`:

1. If the operation succeeds, returns the result
2. If a retryable error occurs, retries up to 2 times
3. If all retries fail:
   - For functions starting with `authorize_`: Returns `False` (deny access)
   - For other functions: Raises the exception

This ensures the system fails securely when the Policy Store is unavailable.

### Error Handling

#### Retryable Errors

Errors that trigger retries:
- Transient service errors (503, 500)
- Throttling errors
- Timeout errors
- Network errors

#### Non-Retryable Errors

Errors that fail immediately:
- Permission errors (403, AccessDenied)
- Not found errors (404, NoSuchKey)
- Invalid request errors (400, ValidationException)
- Resource not found errors

### Testing

Run the test suite:

```bash
cd lambda_functions/common
pytest test_retry_utils.py -v
```

Tests cover:
- Exponential backoff calculation
- Retryable error detection
- Successful operations (no retry)
- Retryable errors (with retry)
- Non-retryable errors (immediate failure)
- Max retries exceeded
- Fail-closed behavior for authorization
- Retry timing and delays

### Integration with Lambda Functions

All Lambda functions in the system use these retry utilities:

- **upload/handler.py**: S3 pre-signed URL generation, DynamoDB metadata creation, AVP authorization
- **download/handler.py**: S3 pre-signed URL generation, DynamoDB metadata retrieval, AVP authorization
- **list/handler.py**: DynamoDB scan, AVP authorization for each document
- **delete/handler.py**: S3 object deletion, DynamoDB metadata deletion, AVP authorization
- **share/handler.py**: DynamoDB metadata update, AVP policy creation, AVP authorization
- **upload_complete/handler.py**: DynamoDB metadata update
- **cleanup/handler.py**: S3 object deletion, DynamoDB scan and deletion

### Logging

Retry operations log detailed information:

```json
{
  "message": "Retry attempt 1/3 for S3:upload_to_s3 after 0.12s delay. Error: ServiceUnavailable"
}
```

Failed retries log:

```json
{
  "message": "Max retries (3) exceeded for DynamoDB:save_metadata"
}
```

Fail-closed authorization logs:

```json
{
  "message": "Policy Store unavailable after retries in authorize_upload: ServiceUnavailable",
  "message": "Implementing fail-closed behavior: denying access"
}
```

### Performance Considerations

- **Latency**: Retries add latency to failed operations. Most operations succeed on first attempt.
- **Cost**: Retries increase API call costs. Configured to balance reliability and cost.
- **Throttling**: Exponential backoff helps avoid overwhelming services during throttling.
- **Jitter**: Randomization prevents synchronized retries from multiple Lambda instances.

### Best Practices

1. **Use decorators**: Simplest and most consistent approach
2. **Don't nest retries**: Apply decorator to lowest-level function making AWS call
3. **Log appropriately**: Retry logic logs automatically, don't duplicate
4. **Test failure scenarios**: Verify retry behavior with unit tests
5. **Monitor metrics**: Track retry rates in CloudWatch to identify issues

### Troubleshooting

**High retry rates**: Check CloudWatch metrics for service throttling or errors

**Timeouts**: Ensure Lambda timeout is sufficient for max retries:
```
timeout >= (base_delay * 2^max_retries) * operation_count + operation_time
```

**Authorization always denied**: Check CloudWatch logs for Policy Store errors

**Inconsistent behavior**: Verify decorator is applied to correct function level

"""
Retry utilities with exponential backoff for AWS operations

Implements retry logic for S3, DynamoDB, and Verified Permissions operations
with exponential backoff and fail-closed behavior.
"""

import time
import functools
from typing import Callable, Any, TypeVar, Optional
from botocore.exceptions import ClientError

T = TypeVar('T')


class RetryConfig:
    """Configuration for retry behavior"""
    
    # S3 retry configuration
    S3_MAX_RETRIES = 3
    S3_BASE_DELAY = 0.1  # 100ms
    S3_MAX_DELAY = 2.0   # 2 seconds
    
    # DynamoDB retry configuration
    DYNAMODB_MAX_RETRIES = 3
    DYNAMODB_BASE_DELAY = 0.1  # 100ms
    DYNAMODB_MAX_DELAY = 2.0   # 2 seconds
    
    # Verified Permissions retry configuration
    AVP_MAX_RETRIES = 2
    AVP_BASE_DELAY = 0.1  # 100ms
    AVP_MAX_DELAY = 1.0   # 1 second
    
    # Retryable error codes
    S3_RETRYABLE_ERRORS = {
        'RequestTimeout',
        'ServiceUnavailable',
        'SlowDown',
        'InternalError',
        '503',
        '500'
    }
    
    DYNAMODB_RETRYABLE_ERRORS = {
        'ProvisionedThroughputExceededException',
        'ThrottlingException',
        'RequestLimitExceeded',
        'InternalServerError',
        'ServiceUnavailable'
    }
    
    AVP_RETRYABLE_ERRORS = {
        'ThrottlingException',
        'ServiceUnavailable',
        'InternalServerError',
        'RequestTimeout'
    }


def calculate_backoff_delay(attempt: int, base_delay: float, max_delay: float) -> float:
    """
    Calculate exponential backoff delay with jitter
    
    Args:
        attempt: Current retry attempt (0-indexed)
        base_delay: Base delay in seconds
        max_delay: Maximum delay in seconds
        
    Returns:
        Delay in seconds
    """
    # Exponential backoff: base_delay * 2^attempt
    delay = base_delay * (2 ** attempt)
    
    # Cap at max_delay
    delay = min(delay, max_delay)
    
    # Add jitter (±25% randomization)
    import random
    jitter = delay * 0.25 * (random.random() * 2 - 1)
    
    return max(0, delay + jitter)


def is_retryable_error(error: ClientError, retryable_codes: set) -> bool:
    """
    Check if error is retryable
    
    Args:
        error: ClientError from boto3
        retryable_codes: Set of retryable error codes
        
    Returns:
        True if error is retryable
    """
    error_code = error.response.get('Error', {}).get('Code', '')
    http_status = error.response.get('ResponseMetadata', {}).get('HTTPStatusCode', 0)
    
    # Check error code
    if error_code in retryable_codes:
        return True
    
    # Check HTTP status code for 5xx errors
    if http_status >= 500:
        return True
    
    return False


def retry_with_backoff(
    max_retries: int,
    base_delay: float,
    max_delay: float,
    retryable_errors: set,
    operation_name: str
) -> Callable:
    """
    Decorator for retrying operations with exponential backoff
    
    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Base delay for exponential backoff
        max_delay: Maximum delay between retries
        retryable_errors: Set of retryable error codes
        operation_name: Name of operation for logging
        
    Returns:
        Decorator function
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_error = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                    
                except ClientError as e:
                    last_error = e
                    
                    # Check if error is retryable
                    if not is_retryable_error(e, retryable_errors):
                        # Non-retryable error, raise immediately
                        print(f"Non-retryable error in {operation_name}: {e}")
                        raise
                    
                    # Check if we have retries left
                    if attempt >= max_retries:
                        # No more retries, raise the error
                        print(f"Max retries ({max_retries}) exceeded for {operation_name}")
                        raise
                    
                    # Calculate backoff delay
                    delay = calculate_backoff_delay(attempt, base_delay, max_delay)
                    
                    # Log retry attempt
                    print(f"Retry attempt {attempt + 1}/{max_retries} for {operation_name} "
                          f"after {delay:.2f}s delay. Error: {e}")
                    
                    # Wait before retrying
                    time.sleep(delay)
                    
                except Exception as e:
                    # Non-ClientError exceptions are not retried
                    print(f"Non-retryable exception in {operation_name}: {e}")
                    raise
            
            # Should never reach here, but raise last error if we do
            if last_error:
                raise last_error
            
        return wrapper
    return decorator


def retry_s3_operation(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator for S3 operations with retry logic
    
    Retries S3 operations up to 3 times with exponential backoff.
    
    Args:
        func: Function to wrap
        
    Returns:
        Wrapped function with retry logic
    """
    return retry_with_backoff(
        max_retries=RetryConfig.S3_MAX_RETRIES,
        base_delay=RetryConfig.S3_BASE_DELAY,
        max_delay=RetryConfig.S3_MAX_DELAY,
        retryable_errors=RetryConfig.S3_RETRYABLE_ERRORS,
        operation_name=f"S3:{func.__name__}"
    )(func)


def retry_dynamodb_operation(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator for DynamoDB operations with retry logic
    
    Retries DynamoDB operations up to 3 times with exponential backoff.
    Handles throttling and provisioned throughput exceptions.
    
    Args:
        func: Function to wrap
        
    Returns:
        Wrapped function with retry logic
    """
    return retry_with_backoff(
        max_retries=RetryConfig.DYNAMODB_MAX_RETRIES,
        base_delay=RetryConfig.DYNAMODB_BASE_DELAY,
        max_delay=RetryConfig.DYNAMODB_MAX_DELAY,
        retryable_errors=RetryConfig.DYNAMODB_RETRYABLE_ERRORS,
        operation_name=f"DynamoDB:{func.__name__}"
    )(func)


def retry_avp_operation(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator for Amazon Verified Permissions operations with retry logic
    
    Retries AVP operations up to 2 times with exponential backoff.
    Implements fail-closed behavior: if all retries fail, denies access.
    
    Args:
        func: Function to wrap
        
    Returns:
        Wrapped function with retry logic
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        try:
            # Apply retry logic
            retried_func = retry_with_backoff(
                max_retries=RetryConfig.AVP_MAX_RETRIES,
                base_delay=RetryConfig.AVP_BASE_DELAY,
                max_delay=RetryConfig.AVP_MAX_DELAY,
                retryable_errors=RetryConfig.AVP_RETRYABLE_ERRORS,
                operation_name=f"AVP:{func.__name__}"
            )(func)
            
            return retried_func(*args, **kwargs)
            
        except ClientError as e:
            # Fail-closed: if Policy Store is unavailable after retries, deny access
            print(f"Policy Store unavailable after retries in {func.__name__}: {e}")
            print("Implementing fail-closed behavior: denying access")
            
            # For authorization functions, return False (deny)
            # For other functions, re-raise the error
            if func.__name__.startswith('authorize_'):
                return False
            else:
                raise
    
    return wrapper


# Convenience function for manual retry logic
def execute_with_retry(
    operation: Callable[[], T],
    operation_type: str,
    max_retries: Optional[int] = None,
    base_delay: Optional[float] = None,
    max_delay: Optional[float] = None
) -> T:
    """
    Execute an operation with retry logic
    
    Args:
        operation: Callable to execute
        operation_type: Type of operation ('s3', 'dynamodb', 'avp')
        max_retries: Optional override for max retries
        base_delay: Optional override for base delay
        max_delay: Optional override for max delay
        
    Returns:
        Result of operation
        
    Raises:
        ClientError: If operation fails after all retries
    """
    # Select configuration based on operation type
    if operation_type == 's3':
        max_retries = max_retries or RetryConfig.S3_MAX_RETRIES
        base_delay = base_delay or RetryConfig.S3_BASE_DELAY
        max_delay = max_delay or RetryConfig.S3_MAX_DELAY
        retryable_errors = RetryConfig.S3_RETRYABLE_ERRORS
    elif operation_type == 'dynamodb':
        max_retries = max_retries or RetryConfig.DYNAMODB_MAX_RETRIES
        base_delay = base_delay or RetryConfig.DYNAMODB_BASE_DELAY
        max_delay = max_delay or RetryConfig.DYNAMODB_MAX_DELAY
        retryable_errors = RetryConfig.DYNAMODB_RETRYABLE_ERRORS
    elif operation_type == 'avp':
        max_retries = max_retries or RetryConfig.AVP_MAX_RETRIES
        base_delay = base_delay or RetryConfig.AVP_BASE_DELAY
        max_delay = max_delay or RetryConfig.AVP_MAX_DELAY
        retryable_errors = RetryConfig.AVP_RETRYABLE_ERRORS
    else:
        raise ValueError(f"Unknown operation type: {operation_type}")
    
    last_error = None
    
    for attempt in range(max_retries + 1):
        try:
            return operation()
            
        except ClientError as e:
            last_error = e
            
            if not is_retryable_error(e, retryable_errors):
                raise
            
            if attempt >= max_retries:
                raise
            
            delay = calculate_backoff_delay(attempt, base_delay, max_delay)
            print(f"Retry attempt {attempt + 1}/{max_retries} for {operation_type} "
                  f"after {delay:.2f}s delay. Error: {e}")
            time.sleep(delay)
    
    if last_error:
        raise last_error

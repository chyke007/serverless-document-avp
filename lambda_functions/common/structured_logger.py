"""
Structured logging module for Lambda functions
Provides consistent log format with timestamp, level, user_id, request_id
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from enum import Enum


class LogLevel(Enum):
    """Log level enumeration"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class EventType(Enum):
    """Event type enumeration for categorizing log entries"""
    # Authentication events
    AUTHENTICATION_SUCCESS = "authentication_success"
    AUTHENTICATION_FAILURE = "authentication_failure"
    AUTHENTICATION_EVENT = "authentication_event"
    
    # Authorization events
    AUTHORIZATION_DECISION = "authorization_decision"
    AUTHORIZATION_DENIED = "authorization_denied"
    AUTHORIZATION_ERROR = "authorization_error"
    
    # Document operation events
    AUDIT_LOG = "audit_log"
    DOCUMENT_UPLOAD = "document_upload"
    DOCUMENT_DOWNLOAD = "document_download"
    DOCUMENT_DELETE = "document_delete"
    DOCUMENT_SHARE = "document_share"
    DOCUMENT_LIST = "document_list"
    
    # System events
    METRIC_EMITTED = "metric_emitted"
    METRIC_ERROR = "metric_error"
    LAMBDA_INVOCATION = "lambda_invocation"
    LAMBDA_ERROR = "lambda_error"
    UNEXPECTED_ERROR = "unexpected_error"
    
    # AWS service events
    S3_OPERATION = "s3_operation"
    S3_ERROR = "s3_error"
    DYNAMODB_OPERATION = "dynamodb_operation"
    DYNAMODB_ERROR = "dynamodb_error"
    AVP_OPERATION = "avp_operation"
    AVP_ERROR = "avp_error"
    
    # Other events
    PRESIGNED_URL_GENERATED = "presigned_url_generated"
    PRESIGNED_URL_ERROR = "presigned_url_error"
    METADATA_RETRIEVED = "metadata_retrieved"
    METADATA_CREATED = "metadata_created"
    METADATA_UPDATED = "metadata_updated"
    POLICY_CREATED = "policy_created"
    POLICY_ERROR = "policy_error"


class StructuredLogger:
    """
    Structured logger for Lambda functions
    
    Provides consistent log format with:
    - timestamp (ISO 8601)
    - level (DEBUG, INFO, WARN, ERROR)
    - event_type (categorization)
    - user_id (if available)
    - request_id (correlation ID)
    - Additional context fields
    """
    
    def __init__(
        self,
        function_name: str,
        request_id: Optional[str] = None,
        user_id: Optional[str] = None
    ):
        """
        Initialize structured logger
        
        Args:
            function_name: Name of the Lambda function
            request_id: Request correlation ID
            user_id: User identifier (if available)
        """
        self.function_name = function_name
        self.request_id = request_id
        self.user_id = user_id
        
        # Configure Python logger
        self.logger = logging.getLogger(function_name)
        self.logger.setLevel(logging.INFO)
    
    def _log(
        self,
        level: LogLevel,
        event_type: EventType,
        message: str,
        **context
    ) -> None:
        """
        Internal log method that formats and outputs structured log entry
        
        Args:
            level: Log level
            event_type: Event type for categorization
            message: Log message
            **context: Additional context fields
        """
        log_entry = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': level.value,
            'event_type': event_type.value,
            'function_name': self.function_name,
            'message': message
        }
        
        # Add request_id if available
        if self.request_id:
            log_entry['request_id'] = self.request_id
        
        # Add user_id if available
        if self.user_id:
            log_entry['user_id'] = self.user_id
        
        # Add additional context
        if context:
            log_entry.update(context)
        
        # Output as JSON
        print(json.dumps(log_entry))
    
    def debug(self, event_type: EventType, message: str, **context) -> None:
        """Log debug message"""
        self._log(LogLevel.DEBUG, event_type, message, **context)
    
    def info(self, event_type: EventType, message: str, **context) -> None:
        """Log info message"""
        self._log(LogLevel.INFO, event_type, message, **context)
    
    def warn(self, event_type: EventType, message: str, **context) -> None:
        """Log warning message"""
        self._log(LogLevel.WARN, event_type, message, **context)
    
    def error(self, event_type: EventType, message: str, **context) -> None:
        """Log error message"""
        self._log(LogLevel.ERROR, event_type, message, **context)
    
    def audit_log(
        self,
        action: str,
        result: str,
        document_id: Optional[str] = None,
        **details
    ) -> None:
        """
        Log audit trail entry for document operations
        
        Args:
            action: Action performed (upload, download, delete, share, list)
            result: Result of operation (success, failure, denied)
            document_id: Document identifier (if applicable)
            **details: Additional details to log
        """
        context = {
            'action': action,
            'result': result
        }
        
        if document_id:
            context['document_id'] = document_id
        
        context.update(details)
        
        self.info(
            EventType.AUDIT_LOG,
            f"Audit: {action} - {result}",
            **context
        )
    
    def authentication_event(
        self,
        outcome: str,
        email: Optional[str] = None,
        **details
    ) -> None:
        """
        Log authentication event
        
        Args:
            outcome: Outcome of authentication (success, failure)
            email: User email (if available)
            **details: Additional details
        """
        context = {
            'outcome': outcome
        }
        
        if email:
            context['email'] = email
        
        context.update(details)
        
        event_type = (
            EventType.AUTHENTICATION_SUCCESS if outcome == 'success'
            else EventType.AUTHENTICATION_FAILURE
        )
        
        self.info(
            event_type,
            f"Authentication {outcome}",
            **context
        )
    
    def authorization_decision(
        self,
        action: str,
        decision: str,
        document_id: Optional[str] = None,
        **details
    ) -> None:
        """
        Log authorization decision
        
        Args:
            action: Action being authorized (read, write, delete, share)
            decision: Authorization decision (ALLOW, DENY)
            document_id: Document identifier (if applicable)
            **details: Additional details (determining_policies, etc.)
        """
        context = {
            'action': action,
            'decision': decision
        }
        
        if document_id:
            context['document_id'] = document_id
        
        context.update(details)
        
        event_type = (
            EventType.AUTHORIZATION_DECISION if decision == 'ALLOW'
            else EventType.AUTHORIZATION_DENIED
        )
        
        level = LogLevel.INFO if decision == 'ALLOW' else LogLevel.WARN
        
        self._log(
            level,
            event_type,
            f"Authorization {decision} for {action}",
            **context
        )
    
    def aws_service_operation(
        self,
        service: str,
        operation: str,
        success: bool,
        **details
    ) -> None:
        """
        Log AWS service operation
        
        Args:
            service: AWS service name (s3, dynamodb, avp)
            operation: Operation performed
            success: Whether operation succeeded
            **details: Additional details
        """
        event_type_map = {
            's3': EventType.S3_OPERATION if success else EventType.S3_ERROR,
            'dynamodb': EventType.DYNAMODB_OPERATION if success else EventType.DYNAMODB_ERROR,
            'avp': EventType.AVP_OPERATION if success else EventType.AVP_ERROR
        }
        
        event_type = event_type_map.get(service.lower(), EventType.LAMBDA_ERROR)
        level = LogLevel.INFO if success else LogLevel.ERROR
        
        context = {
            'service': service,
            'operation': operation,
            'success': success
        }
        context.update(details)
        
        self._log(
            level,
            event_type,
            f"{service} {operation} {'succeeded' if success else 'failed'}",
            **context
        )
    
    def metric_emitted(self, metric_name: str, value: float, **dimensions) -> None:
        """
        Log custom metric emission
        
        Args:
            metric_name: Name of the metric
            value: Metric value
            **dimensions: Metric dimensions
        """
        self.info(
            EventType.METRIC_EMITTED,
            f"Metric emitted: {metric_name}",
            metric_name=metric_name,
            value=value,
            dimensions=dimensions
        )
    
    def set_user_id(self, user_id: str) -> None:
        """Update user_id for subsequent log entries"""
        self.user_id = user_id
    
    def set_request_id(self, request_id: str) -> None:
        """Update request_id for subsequent log entries"""
        self.request_id = request_id


def create_logger(
    function_name: str,
    context: Any = None,
    user_id: Optional[str] = None
) -> StructuredLogger:
    """
    Factory function to create structured logger
    
    Args:
        function_name: Name of the Lambda function
        context: Lambda context object (optional)
        user_id: User identifier (optional)
        
    Returns:
        StructuredLogger instance
    """
    # Extract request ID from Lambda context
    request_id = None
    if context:
        try:
            # Try aws_request_id first (correct attribute name)
            request_id = getattr(context, 'aws_request_id', None)
            # Fallback to request_id if aws_request_id doesn't exist
            if not request_id:
                request_id = getattr(context, 'request_id', None)
        except (AttributeError, TypeError):
            pass
    
    return StructuredLogger(function_name, request_id, user_id)

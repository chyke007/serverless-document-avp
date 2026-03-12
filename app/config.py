"""Configuration management for Streamlit application"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class AppConfig:
    """Application configuration"""
    api_gateway_url: str
    cognito_user_pool_id: str
    cognito_client_id: str
    cognito_region: str
    session_table_name: str
    aws_region: str
    
    @classmethod
    def from_env(cls) -> 'AppConfig':
        """Load configuration from environment variables"""
        return cls(
            api_gateway_url=os.getenv('API_GATEWAY_URL', ''),
            cognito_user_pool_id=os.getenv('COGNITO_USER_POOL_ID', ''),
            cognito_client_id=os.getenv('COGNITO_CLIENT_ID', ''),
            cognito_region=os.getenv('COGNITO_REGION', os.getenv('AWS_REGION', 'us-east-1')),
            session_table_name=os.getenv('SESSION_TABLE_NAME', 'StreamlitSessions'),
            aws_region=os.getenv('AWS_REGION', 'us-east-1')
        )
    
    def validate(self) -> tuple[bool, Optional[str]]:
        """Validate configuration"""
        if not self.api_gateway_url:
            return False, "API_GATEWAY_URL not configured"
        if not self.cognito_user_pool_id:
            return False, "COGNITO_USER_POOL_ID not configured"
        if not self.cognito_client_id:
            return False, "COGNITO_CLIENT_ID not configured"
        if not self.session_table_name:
            return False, "SESSION_TABLE_NAME not configured"
        return True, None

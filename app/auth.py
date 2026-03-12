"""Cognito authentication integration"""

import boto3
import hmac
import hashlib
import base64
from typing import Optional, Dict, Any
from dataclasses import dataclass
import streamlit as st


@dataclass
class CognitoTokens:
    """Cognito authentication tokens"""
    access_token: str
    id_token: str
    refresh_token: str
    token_type: str = "Bearer"


@dataclass
class UserInfo:
    """User information from Cognito"""
    user_id: str
    email: str
    role: str
    department: str


class CognitoAuth:
    """Handles Cognito authentication operations"""
    
    def __init__(self, user_pool_id: str, client_id: str, region: str, client_secret: Optional[str] = None):
        self.user_pool_id = user_pool_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.region = region
        self.client = boto3.client('cognito-idp', region_name=region)
    
    def _get_secret_hash(self, username: str) -> Optional[str]:
        """Generate secret hash for Cognito authentication"""
        if not self.client_secret:
            return None
        
        message = username + self.client_id
        dig = hmac.new(
            self.client_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
        return base64.b64encode(dig).decode()
    
    def sign_in(self, username: str, password: str) -> tuple[bool, Optional[CognitoTokens], Optional[str]]:
        """
        Sign in user with username and password
        
        Returns:
            (success, tokens, error_message)
        """
        try:
            auth_params = {
                'USERNAME': username,
                'PASSWORD': password
            }
            
            # Add secret hash if client secret is configured
            secret_hash = self._get_secret_hash(username)
            if secret_hash:
                auth_params['SECRET_HASH'] = secret_hash
            
            response = self.client.initiate_auth(
                ClientId=self.client_id,
                AuthFlow='USER_PASSWORD_AUTH',
                AuthParameters=auth_params
            )
            
            # Check if MFA is required
            if response.get('ChallengeName') == 'SMS_MFA' or response.get('ChallengeName') == 'SOFTWARE_TOKEN_MFA':
                return False, None, "MFA_REQUIRED"
            
            # Check if new password is required (FORCE_CHANGE_PASSWORD status)
            if response.get('ChallengeName') == 'NEW_PASSWORD_REQUIRED':
                # For users with FORCE_CHANGE_PASSWORD status, we need to respond to the challenge
                # However, since we're setting permanent passwords now, this shouldn't happen
                # But we'll handle it gracefully
                return False, None, "NEW_PASSWORD_REQUIRED: User must change password. Please contact admin to reset password."
            
            auth_result = response.get('AuthenticationResult', {})
            
            # Ensure we have all required tokens
            if not auth_result.get('AccessToken'):
                return False, None, "Authentication error: Missing access token"
            
            tokens = CognitoTokens(
                access_token=auth_result['AccessToken'],
                id_token=auth_result['IdToken'],
                refresh_token=auth_result['RefreshToken']
            )
            
            return True, tokens, None
            
        except self.client.exceptions.NotAuthorizedException:
            return False, None, "Invalid username or password"
        except self.client.exceptions.UserNotFoundException:
            return False, None, "User not found"
        except self.client.exceptions.UserNotConfirmedException:
            return False, None, "User email not verified"
        except Exception as e:
            return False, None, f"Authentication error: {str(e)}"
    
    def sign_out(self, access_token: str) -> tuple[bool, Optional[str]]:
        """
        Sign out user and invalidate tokens
        
        Returns:
            (success, error_message)
        """
        try:
            self.client.global_sign_out(AccessToken=access_token)
            return True, None
        except Exception as e:
            return False, f"Sign out error: {str(e)}"
    
    def get_user_info(self, access_token: str, id_token: Optional[str] = None) -> tuple[bool, Optional[UserInfo], Optional[str]]:
        """
        Get user information from access token
        
        Args:
            access_token: Cognito access token
            id_token: Cognito ID token (optional, used to extract sub)
        
        Returns:
            (success, user_info, error_message)
        """
        try:
            response = self.client.get_user(AccessToken=access_token)
            
            # Extract user attributes
            attributes = {attr['Name']: attr['Value'] for attr in response['UserAttributes']}
            
            # Extract sub (Cognito user ID) from ID token if available
            # Otherwise fall back to Username, but prefer sub for Cedar compatibility
            user_id = response['Username']  # Default fallback
            
            if id_token:
                try:
                    # Decode JWT token to extract sub
                    # ID token is a JWT with three parts separated by dots
                    import base64
                    import json
                    
                    # Split the token into parts
                    parts = id_token.split('.')
                    if len(parts) >= 2:
                        # Decode the payload (second part)
                        # Add padding if needed for base64 decoding
                        payload = parts[1]
                        padding = len(payload) % 4
                        if padding:
                            payload += '=' * (4 - padding)
                        
                        decoded_bytes = base64.urlsafe_b64decode(payload)
                        decoded_token = json.loads(decoded_bytes.decode('utf-8'))
                        user_id = decoded_token.get('sub', user_id)
                except Exception:
                    # If ID token decode fails, use Username as fallback
                    pass
            
            # Also check if sub is in the attributes (some Cognito setups include it)
            if 'sub' in attributes:
                user_id = attributes['sub']
            
            user_info = UserInfo(
                user_id=user_id,  # Use sub (Cognito UUID) instead of Username
                email=attributes.get('email', ''),
                role=attributes.get('custom:role', 'Viewer'),
                department=attributes.get('custom:department', '')
            )
            
            return True, user_info, None
            
        except self.client.exceptions.NotAuthorizedException:
            return False, None, "Token expired or invalid"
        except Exception as e:
            return False, None, f"Error getting user info: {str(e)}"
    
    def refresh_tokens(self, refresh_token: str) -> tuple[bool, Optional[CognitoTokens], Optional[str]]:
        """
        Refresh access token using refresh token
        
        Returns:
            (success, tokens, error_message)
        """
        try:
            auth_params = {
                'REFRESH_TOKEN': refresh_token
            }
            
            response = self.client.initiate_auth(
                ClientId=self.client_id,
                AuthFlow='REFRESH_TOKEN_AUTH',
                AuthParameters=auth_params
            )
            
            auth_result = response.get('AuthenticationResult', {})
            tokens = CognitoTokens(
                access_token=auth_result['AccessToken'],
                id_token=auth_result['IdToken'],
                refresh_token=refresh_token  # Refresh token doesn't change
            )
            
            return True, tokens, None
            
        except Exception as e:
            return False, None, f"Token refresh error: {str(e)}"


def init_session_state():
    """Initialize session state for authentication"""
    if 'authenticated' not in st.session_state:
        st.session_state.authenticated = False
    if 'tokens' not in st.session_state:
        st.session_state.tokens = None
    if 'user_info' not in st.session_state:
        st.session_state.user_info = None
    
    # Initialize session manager for persistence
    if 'session_manager' not in st.session_state:
        try:
            from session_manager import init_session_persistence
            st.session_state.session_manager = init_session_persistence()

            # Try to restore session from DynamoDB on page refresh
            if not st.session_state.authenticated and st.session_state.session_manager:
                restored = st.session_state.session_manager.restore_session_to_streamlit()

                # If we restored something, verify / refresh tokens as needed
                if restored and st.session_state.tokens and 'auth_client' in st.session_state:
                    auth_client = st.session_state.auth_client
                    tokens = st.session_state.tokens

                    # First, try to validate current access token
                    success, user_info, error = auth_client.get_user_info(
                        tokens.access_token,
                        id_token=tokens.id_token if tokens else None
                    )

                    if success and user_info:
                        st.session_state.authenticated = True
                        st.session_state.user_info = user_info
                    else:
                        # Access token likely expired; try refresh with refresh_token
                        if tokens.refresh_token:
                            refresh_ok, new_tokens, refresh_error = auth_client.refresh_tokens(tokens.refresh_token)
                            if refresh_ok and new_tokens:
                                # Update tokens in session state
                                st.session_state.tokens = new_tokens

                                # Re-validate user info with new access token
                                success2, user_info2, error2 = auth_client.get_user_info(
                                    new_tokens.access_token,
                                    id_token=new_tokens.id_token
                                )
                                if success2 and user_info2:
                                    st.session_state.authenticated = True
                                    st.session_state.user_info = user_info2

                                    # Persist refreshed session if session manager is available
                                    if 'session_manager' in st.session_state:
                                        st.session_state.session_manager.persist_streamlit_session()
                                else:
                                    # Could not validate even after refresh
                                    clear_session()
                            else:
                                # Refresh failed; clear session
                                clear_session()
                        else:
                            # No refresh token available; clear session
                            clear_session()
        except Exception as e:
            # Session manager not available or error, continue without persistence
            pass


def is_authenticated() -> bool:
    """Check if user is authenticated"""
    return st.session_state.get('authenticated', False)


def get_access_token() -> Optional[str]:
    """Get current Cognito access token (JWT) for API calls"""
    tokens = st.session_state.get('tokens')
    return tokens.access_token if tokens else None


def get_id_token() -> Optional[str]:
    """Get current Cognito ID token (JWT) (rarely used for API calls)"""
    tokens = st.session_state.get('tokens')
    return tokens.id_token if tokens else None


def get_bearer_token() -> Optional[str]:
    """
    Backward/compat helper: the token we attach to API Gateway requests.
    For this app we use the Cognito ID token.
    """
    return get_id_token()


def get_user_info() -> Optional[UserInfo]:
    """Get current user info"""
    return st.session_state.get('user_info')


def clear_session():
    """Clear authentication session"""
    st.session_state.authenticated = False
    st.session_state.tokens = None
    st.session_state.user_info = None
    
    # Clear persistent session from DynamoDB
    if 'session_manager' in st.session_state:
        st.session_state.session_manager.delete_session()


def get_user_sub_from_email(email: str, user_pool_id: str, region: str) -> Optional[str]:
    """
    Get Cognito user sub (UUID) from email address
    
    Args:
        email: User email address
        user_pool_id: Cognito User Pool ID
        region: AWS region
        
    Returns:
        User sub (UUID) if found, None otherwise
    """
    try:
        client = boto3.client('cognito-idp', region_name=region)
        
        # List users with email filter
        response = client.list_users(
            UserPoolId=user_pool_id,
            Filter=f'email = "{email}"',
            Limit=1
        )
        
        users = response.get('Users', [])
        if not users:
            return None
        
        # Get the username (which is the email when email is used as sign-in alias)
        username = users[0].get('Username', '')
        
        if not username:
            return None
        
        # Use admin_get_user to get full user details including sub
        # list_users doesn't return sub in attributes, so we need admin_get_user
        try:
            admin_response = client.admin_get_user(
                UserPoolId=user_pool_id,
                Username=username
            )
            
            # Extract sub from user attributes
            admin_attributes = {attr['Name']: attr['Value'] for attr in admin_response.get('UserAttributes', [])}
            sub = admin_attributes.get('sub')
            
            # If sub not found in attributes, check if Username itself is a UUID
            if not sub:
                uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                if re.match(uuid_pattern, username, re.IGNORECASE):
                    sub = username
            
            return sub
            
        except Exception as e:
            # Fallback: check if username is already a UUID
            uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            if re.match(uuid_pattern, username, re.IGNORECASE):
                return username
            return None
        
    except Exception as e:
        print(f"Error getting user sub from email: {str(e)}")
        return None

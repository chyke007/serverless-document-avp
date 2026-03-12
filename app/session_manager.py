"""Session persistence manager for Streamlit using DynamoDB"""

import boto3
import json
import time
import uuid
import os
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
import streamlit as st


@dataclass
class SessionData:
    """Session data structure"""
    session_id: str
    authenticated: bool
    user_id: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None
    access_token: Optional[str] = None
    id_token: Optional[str] = None
    refresh_token: Optional[str] = None
    created_at: Optional[str] = None
    last_accessed: Optional[str] = None
    ttl: Optional[int] = None


class SessionManager:
    """Manages persistent sessions using DynamoDB"""
    
    def __init__(self, table_name: Optional[str] = None, region: Optional[str] = None):
        """
        Initialize session manager
        
        Args:
            table_name: DynamoDB table name for session storage
            region: AWS region
        """
        self.table_name = table_name or os.getenv('SESSION_TABLE_NAME', 'StreamlitSessions')
        self.region = region or os.getenv('AWS_REGION', 'us-east-1')
        
        # Initialize DynamoDB client
        self.dynamodb = boto3.resource('dynamodb', region_name=self.region)
        self.table = self.dynamodb.Table(self.table_name)
        
        # Session TTL: 8 hours (matches typical work session)
        self.session_ttl_hours = 8
    
    def get_or_create_session_id(self) -> str:
        """
        Get existing session ID from Streamlit session state or create new one
        
        Returns:
            Session ID
        """
        # Handle both dict-like and object-like session state
        if hasattr(st.session_state, 'persistent_session_id'):
            return st.session_state.persistent_session_id
        elif hasattr(st.session_state, 'get'):
            session_id = st.session_state.get('persistent_session_id')
            if session_id:
                return session_id
        
        # Create new session ID
        session_id = str(uuid.uuid4())
        st.session_state.persistent_session_id = session_id
        return session_id
    
    def save_session(self, session_data: Dict[str, Any]) -> bool:
        """
        Save session data to DynamoDB
        
        Args:
            session_data: Dictionary containing session data
            
        Returns:
            True if successful, False otherwise
        """
        try:
            session_id = self.get_or_create_session_id()
            
            # Calculate TTL (8 hours from now)
            ttl = int((datetime.utcnow() + timedelta(hours=self.session_ttl_hours)).timestamp())
            
            # Prepare item for DynamoDB
            item = {
                'session_id': session_id,
                'authenticated': session_data.get('authenticated', False),
                'user_id': session_data.get('user_id'),
                'email': session_data.get('email'),
                'role': session_data.get('role'),
                'department': session_data.get('department'),
                'access_token': session_data.get('access_token'),
                'id_token': session_data.get('id_token'),
                'refresh_token': session_data.get('refresh_token'),
                'created_at': session_data.get('created_at', datetime.utcnow().isoformat()),
                'last_accessed': datetime.utcnow().isoformat(),
                'ttl': ttl,
            }
            
            # Remove None values
            item = {k: v for k, v in item.items() if v is not None}
            
            # Save to DynamoDB
            self.table.put_item(Item=item)
            
            return True
            
        except Exception as e:
            print(f"Error saving session: {str(e)}")
            return False
    
    def load_session(self) -> Optional[Dict[str, Any]]:
        """
        Load session data from DynamoDB
        
        Returns:
            Session data dictionary or None if not found
        """
        try:
            session_id = self.get_or_create_session_id()
            
            # Get item from DynamoDB
            response = self.table.get_item(Key={'session_id': session_id})
            
            if 'Item' not in response:
                return None
            
            item = response['Item']
            
            # Check if session has expired
            if 'ttl' in item:
                if int(item['ttl']) < int(time.time()):
                    # Session expired, delete it
                    self.delete_session()
                    return None
            
            # Update last accessed time
            self.table.update_item(
                Key={'session_id': session_id},
                UpdateExpression='SET last_accessed = :timestamp',
                ExpressionAttributeValues={
                    ':timestamp': datetime.utcnow().isoformat()
                }
            )
            
            return item
            
        except Exception as e:
            print(f"Error loading session: {str(e)}")
            return None
    
    def delete_session(self) -> bool:
        """
        Delete session from DynamoDB
        
        Returns:
            True if successful, False otherwise
        """
        try:
            session_id = self.get_or_create_session_id()
            
            # Delete from DynamoDB
            self.table.delete_item(Key={'session_id': session_id})
            
            # Clear session ID from Streamlit session state
            if hasattr(st.session_state, 'persistent_session_id'):
                delattr(st.session_state, 'persistent_session_id')
            elif hasattr(st.session_state, '__delitem__'):
                try:
                    del st.session_state['persistent_session_id']
                except KeyError:
                    pass
            
            return True
            
        except Exception as e:
            print(f"Error deleting session: {str(e)}")
            return False
    
    def restore_session_to_streamlit(self) -> bool:
        """
        Restore session data from DynamoDB to Streamlit session state
        
        Returns:
            True if session was restored, False otherwise
        """
        try:
            # Load session from DynamoDB
            session_data = self.load_session()
            
            if not session_data:
                return False
            
            # Restore to Streamlit session state
            if session_data.get('authenticated'):
                st.session_state.authenticated = True
                
                # Restore user info
                if session_data.get('user_id'):
                    from auth import UserInfo
                    st.session_state.user_info = UserInfo(
                        user_id=session_data['user_id'],
                        email=session_data.get('email', ''),
                        role=session_data.get('role', 'Viewer'),
                        department=session_data.get('department', '')
                    )
                
                # Restore tokens
                if session_data.get('access_token'):
                    from auth import CognitoTokens
                    st.session_state.tokens = CognitoTokens(
                        access_token=session_data['access_token'],
                        id_token=session_data.get('id_token', ''),
                        refresh_token=session_data.get('refresh_token', '')
                    )
                
                return True
            
            return False
            
        except Exception as e:
            print(f"Error restoring session: {str(e)}")
            return False
    
    def persist_streamlit_session(self) -> bool:
        """
        Persist current Streamlit session state to DynamoDB
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Check if user is authenticated
            if not st.session_state.get('authenticated', False):
                return False
            
            # Prepare session data
            session_data = {
                'authenticated': True,
            }
            
            # Add user info if available
            user_info = st.session_state.get('user_info')
            if user_info:
                session_data['user_id'] = user_info.user_id
                session_data['email'] = user_info.email
                session_data['role'] = user_info.role
                session_data['department'] = user_info.department
            
            # Add tokens if available
            tokens = st.session_state.get('tokens')
            if tokens:
                session_data['access_token'] = tokens.access_token
                session_data['id_token'] = tokens.id_token
                session_data['refresh_token'] = tokens.refresh_token
            
            # Save to DynamoDB
            return self.save_session(session_data)
            
        except Exception as e:
            print(f"Error persisting session: {str(e)}")
            return False


def init_session_persistence() -> SessionManager:
    """
    Initialize session persistence
    
    Returns:
        SessionManager instance
    """
    # Create session manager
    session_manager = SessionManager()
    
    # Try to restore session from DynamoDB
    # This happens on container restart or new container
    if not st.session_state.get('authenticated', False):
        session_manager.restore_session_to_streamlit()
    
    return session_manager


def persist_session_on_change():
    """
    Callback to persist session when authentication state changes
    """
    if 'session_manager' in st.session_state:
        st.session_state.session_manager.persist_streamlit_session()

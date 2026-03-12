"""Admin panel page"""

import streamlit as st
import sys
import os

# Add parent directory to path to import from app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import get_bearer_token, get_user_info, init_session_state, is_authenticated
from config import AppConfig
from api_client import APIClient
import boto3
from botocore.exceptions import ClientError

# Initialize session state if not already done
if 'initialized' not in st.session_state:
    init_session_state()
    
    # Load configuration
    config = AppConfig.from_env()
    st.session_state.config = config
    
    # Initialize clients
    from auth import CognitoAuth
    if 'auth_client' not in st.session_state:
        st.session_state.auth_client = CognitoAuth(
            user_pool_id=config.cognito_user_pool_id,
            client_id=config.cognito_client_id,
            region=config.cognito_region
        )
    
    if 'api_client' not in st.session_state:
        st.session_state.api_client = APIClient(base_url=config.api_gateway_url)
    
    st.session_state.initialized = True


def show():
    """Display admin panel page"""
    # Check authentication when accessed as standalone page
    if not is_authenticated():
        st.error("Please sign in to access admin panel")
        st.info("Go to the main app page to sign in")
        return
    
    st.title("🔧 Admin Panel")
    
    access_token = get_bearer_token()
    user_info = get_user_info()
    
    if not access_token or not user_info:
        st.error("Authentication required")
        return
    
    # Check if user is admin
    if user_info.role != "Admin":
        st.error("⛔ Access Denied")
        st.warning("This page is only accessible to users with Admin role")
        return
    
    st.write("Administrative functions for user and system management")
    
    # Tabs for different admin functions
    tab1, tab2, tab3 = st.tabs(["👥 User Management", "📊 System Stats", "⚙️ Settings"])
    
    with tab1:
        show_user_management()
    
    with tab2:
        show_system_stats()
    
    with tab3:
        show_settings()


def show_user_management():
    """Display user management interface"""
    st.subheader("User Management")
    
    # Get Cognito client
    config = st.session_state.get('config')
    if not config:
        from config import AppConfig
        config = AppConfig.from_env()
    
    try:
        cognito_client = boto3.client('cognito-idp', region_name=config.cognito_region)
        
        # List users
        st.write("### Current Users")
        
        with st.spinner("Loading users..."):
            try:
                response = cognito_client.list_users(
                    UserPoolId=config.cognito_user_pool_id,
                    Limit=60
                )
                
                users = response.get('Users', [])
                
                if not users:
                    st.info("No users found")
                else:
                    st.caption(f"Showing {len(users)} user(s)")
                    
                    for user in users:
                        display_user_info(user, cognito_client, config.cognito_user_pool_id)
                        
            except ClientError as e:
                st.error(f"❌ Error loading users: {e.response['Error']['Message']}")
                st.info("Note: Ensure the application has appropriate IAM permissions to list Cognito users")
        
        # Create new user
        st.divider()
        st.write("### Create New User")
        
        with st.form("create_user_form"):
            col1, col2 = st.columns(2)
            
            with col1:
                new_email = st.text_input("Email", placeholder="user@example.com")
                new_role = st.selectbox("Role", ["Admin", "Manager", "Editor", "Viewer"])
            
            with col2:
                new_department = st.text_input("Department", placeholder="Engineering")
                temp_password = st.text_input("Temporary Password", type="password", 
                                             help="User will be required to change on first login")
            
            create_user = st.form_submit_button("➕ Create User", use_container_width=True)
            
            if create_user:
                if not new_email or not temp_password:
                    st.error("❌ Email and password are required")
                else:
                    create_cognito_user(
                        cognito_client,
                        config.cognito_user_pool_id,
                        new_email,
                        temp_password,
                        new_role,
                        new_department
                    )
    
    except Exception as e:
        st.error(f"❌ Error initializing Cognito client: {str(e)}")
        st.info("Ensure AWS credentials are configured correctly")


def display_user_info(user: dict, cognito_client, user_pool_id: str):
    """Display information for a single user"""
    
    # Extract user attributes
    attributes = {attr['Name']: attr['Value'] for attr in user.get('Attributes', [])}
    
    username = user.get('Username', 'Unknown')
    email = attributes.get('email', 'No email')
    role = attributes.get('custom:role', 'No role')
    department = attributes.get('custom:department', 'No department')
    status = user.get('UserStatus', 'Unknown')
    enabled = user.get('Enabled', True)
    
    with st.expander(f"👤 {email} ({role})"):
        col1, col2 = st.columns(2)
        
        with col1:
            st.write(f"**Username:** {username}")
            st.write(f"**Email:** {email}")
            st.write(f"**Role:** {role}")
            st.write(f"**Department:** {department}")
        
        with col2:
            st.write(f"**Status:** {status}")
            st.write(f"**Enabled:** {'Yes' if enabled else 'No'}")
            
            created = user.get('UserCreateDate')
            if created:
                st.write(f"**Created:** {created.strftime('%Y-%m-%d %H:%M')}")
        
        # Admin actions
        st.divider()
        st.write("**Actions:**")
        
        action_col1, action_col2, action_col3 = st.columns(3)
        
        with action_col1:
            if st.button("🔄 Reset Password", key=f"reset_{username}"):
                reset_user_password(cognito_client, user_pool_id, username)
        
        with action_col2:
            if enabled:
                if st.button("🚫 Disable", key=f"disable_{username}"):
                    disable_user(cognito_client, user_pool_id, username)
            else:
                if st.button("✅ Enable", key=f"enable_{username}"):
                    enable_user(cognito_client, user_pool_id, username)
        
        with action_col3:
            delete_key = f"show_delete_{username}"
            if st.button("🗑️ Delete", key=f"delete_{username}"):
                st.session_state[delete_key] = True
                st.rerun()
            
            # Show confirmation dialog if delete was clicked
            if st.session_state.get(delete_key, False):
                delete_user(cognito_client, user_pool_id, username, delete_key)


def create_cognito_user(cognito_client, user_pool_id: str, email: str, password: str, role: str, department: str):
    """Create a new Cognito user with permanent password (no force change required)"""
    
    try:
        with st.spinner(f"Creating user {email}..."):
            # Create user without temporary password (will set permanent password next)
            # This creates user in FORCE_CHANGE_PASSWORD status initially
            response = cognito_client.admin_create_user(
                UserPoolId=user_pool_id,
                Username=email,
                UserAttributes=[
                    {'Name': 'email', 'Value': email},
                    {'Name': 'email_verified', 'Value': 'true'},
                    {'Name': 'custom:role', 'Value': role},
                    {'Name': 'custom:department', 'Value': department}
                ],
                MessageAction='SUPPRESS'  # Don't send welcome email
            )
            
            # Set permanent password (this changes status from FORCE_CHANGE_PASSWORD to CONFIRMED)
            # When Permanent=True, the user is automatically confirmed and can log in immediately
            cognito_client.admin_set_user_password(
                UserPoolId=user_pool_id,
                Username=email,
                Password=password,
                Permanent=True  # Set as permanent password - user will be CONFIRMED automatically
            )
            
            st.success(f"✅ User {email} created successfully!")
            st.info("User can now log in immediately with the provided password")
            st.rerun()
            
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', '')
        error_message = e.response.get('Error', {}).get('Message', str(e))
        
        # Handle case where user already exists
        if error_code == 'UsernameExistsException':
            st.error(f"❌ User {email} already exists")
        elif error_code == 'InvalidPasswordException':
            st.error(f"❌ Password does not meet requirements: {error_message}")
        else:
            st.error(f"❌ Error creating user: {error_message}")


def reset_user_password(cognito_client, user_pool_id: str, username: str):
    """Reset user password"""
    
    try:
        with st.spinner(f"Resetting password for {username}..."):
            cognito_client.admin_reset_user_password(
                UserPoolId=user_pool_id,
                Username=username
            )
            
            st.success(f"✅ Password reset for {username}. User will receive reset instructions.")
            
    except ClientError as e:
        st.error(f"❌ Error resetting password: {e.response['Error']['Message']}")


def disable_user(cognito_client, user_pool_id: str, username: str):
    """Disable user account"""
    
    try:
        with st.spinner(f"Disabling user {username}..."):
            cognito_client.admin_disable_user(
                UserPoolId=user_pool_id,
                Username=username
            )
            
            st.success(f"✅ User {username} disabled")
            st.rerun()
            
    except ClientError as e:
        st.error(f"❌ Error disabling user: {e.response['Error']['Message']}")


def enable_user(cognito_client, user_pool_id: str, username: str):
    """Enable user account"""
    
    try:
        with st.spinner(f"Enabling user {username}..."):
            cognito_client.admin_enable_user(
                UserPoolId=user_pool_id,
                Username=username
            )
            
            st.success(f"✅ User {username} enabled")
            st.rerun()
            
    except ClientError as e:
        st.error(f"❌ Error enabling user: {e.response['Error']['Message']}")


def delete_user(cognito_client, user_pool_id: str, username: str, delete_key: str):
    """Delete user account with confirmation"""
    
    # Confirmation dialog
    st.warning(f"⚠️ Are you sure you want to delete user **{username}**?")
    st.caption("This action cannot be undone.")
    
    col1, col2 = st.columns(2)
    
    with col1:
        confirm_key = f"confirm_delete_{username}"
        if st.button("✅ Yes, Delete", key=confirm_key, use_container_width=True):
            try:
                with st.spinner(f"Deleting user {username}..."):
                    cognito_client.admin_delete_user(
                        UserPoolId=user_pool_id,
                        Username=username
                    )
                    
                    # Clear the delete state
                    if delete_key in st.session_state:
                        del st.session_state[delete_key]
                    
                    st.success(f"✅ User {username} deleted successfully")
                    st.rerun()
                    
            except ClientError as e:
                st.error(f"❌ Error deleting user: {e.response['Error']['Message']}")
                # Keep the form open if deletion failed
                st.session_state[delete_key] = True
    
    with col2:
        cancel_key = f"cancel_delete_{username}"
        if st.button("❌ Cancel", key=cancel_key, use_container_width=True):
            # Clear the delete state
            if delete_key in st.session_state:
                del st.session_state[delete_key]
            st.rerun()


def show_system_stats():
    """Display system statistics"""
    st.subheader("System Statistics")
    
    access_token = get_bearer_token()
    api_client = st.session_state.api_client
    
    # Load all documents
    with st.spinner("Loading system statistics..."):
        success, list_response, error = api_client.list_documents(
            access_token=access_token
        )
    
    if not success:
        st.error(f"❌ Error loading statistics: {error}")
        return
    
    documents = list_response.documents if list_response else []
    
    # Display metrics
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Documents", len(documents))
    
    with col2:
        total_size = sum(doc.size_bytes for doc in documents)
        st.metric("Total Storage", format_file_size(total_size))
    
    with col3:
        shared_docs = len([doc for doc in documents if doc.shared_with])
        st.metric("Shared Documents", shared_docs)
    
    with col4:
        departments = set(doc.department for doc in documents if doc.department)
        st.metric("Departments", len(departments))
    
    # Document distribution by department
    if documents:
        st.divider()
        st.write("### Documents by Department")
        
        dept_counts = {}
        for doc in documents:
            dept = doc.department or "No Department"
            dept_counts[dept] = dept_counts.get(dept, 0) + 1
        
        for dept, count in sorted(dept_counts.items(), key=lambda x: x[1], reverse=True):
            st.write(f"**{dept}:** {count} document(s)")


def show_settings():
    """Display system settings"""
    st.subheader("System Settings")
    
    st.info("⚙️ System settings management coming soon")
    
    st.write("### Current Configuration")
    
    from config import AppConfig
    config = AppConfig.from_env()
    
    st.write(f"**API Gateway URL:** {config.api_gateway_url}")
    st.write(f"**Cognito User Pool ID:** {config.cognito_user_pool_id}")
    st.write(f"**Cognito Client ID:** {config.cognito_client_id}")
    st.write(f"**AWS Region:** {config.cognito_region}")


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


# When accessed as a standalone Streamlit page, automatically call show()
# Only call show() if not being imported by app.py (which calls show() itself)
if 'current_page' not in st.session_state or st.session_state.get('current_page') != 'admin':
    # This page is being accessed directly via Streamlit navigation
    show()

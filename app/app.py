"""Streamlit web application for Document Management System"""

import streamlit as st
from config import AppConfig
from auth import CognitoAuth, init_session_state, is_authenticated, clear_session, get_user_info
from api_client import APIClient

# Page configuration
st.set_page_config(
    page_title="Document Management System",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize session state
init_session_state()

# Load configuration
config = AppConfig.from_env()
st.session_state.config = config  # Store config in session state

valid, error_msg = config.validate()

if not valid:
    st.error(f"⚠️ Configuration Error: {error_msg}")
    st.info("""
    Please set the following environment variables:
    - API_GATEWAY_URL
    - COGNITO_USER_POOL_ID
    - COGNITO_CLIENT_ID
    - AWS_REGION (optional, defaults to us-east-1)
    """)
    st.stop()

# Initialize clients
if 'auth_client' not in st.session_state:
    st.session_state.auth_client = CognitoAuth(
        user_pool_id=config.cognito_user_pool_id,
        client_id=config.cognito_client_id,
        region=config.cognito_region
    )

if 'api_client' not in st.session_state:
    st.session_state.api_client = APIClient(base_url=config.api_gateway_url)

# Initialize current page in session state
if 'current_page' not in st.session_state:
    st.session_state.current_page = "documents"


def show_login_page():
    """Display login page"""
    st.title("📄 Document Management System")
    st.subheader("Sign In")
    
    with st.form("login_form"):
        username = st.text_input("Email", placeholder="user@example.com")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Sign In", use_container_width=True)
        
        if submit:
            if not username or not password:
                st.error("Please enter both email and password")
            else:
                with st.spinner("Authenticating..."):
                    success, tokens, error = st.session_state.auth_client.sign_in(username, password)
                    
                    if success and tokens:
                        # Get user info (pass ID token to extract sub)
                        user_success, user_info, user_error = st.session_state.auth_client.get_user_info(
                            tokens.access_token,
                            id_token=tokens.id_token
                        )
                        
                        if user_success and user_info:
                            st.session_state.authenticated = True
                            st.session_state.tokens = tokens
                            st.session_state.user_info = user_info
                            
                            # Persist session to DynamoDB for container restart resilience
                            if 'session_manager' in st.session_state:
                                st.session_state.session_manager.persist_streamlit_session()
                            
                            st.success("✅ Signed in successfully!")
                            st.rerun()
                        else:
                            st.error(f"❌ Error getting user info: {user_error}")
                    elif error == "MFA_REQUIRED":
                        st.warning("⚠️ MFA verification required (not yet implemented)")
                    else:
                        st.error(f"❌ {error}")
    
    st.divider()
    st.caption("AWS Document Management System - Secure document storage with fine-grained access control")


def show_sidebar():
    """Display sidebar with navigation and user info"""
    with st.sidebar:
        user_info = get_user_info()
        
        if user_info:
            st.success(f"👤 {user_info.email}")
            st.caption(f"Role: {user_info.role}")
            if user_info.department:
                st.caption(f"Department: {user_info.department}")
            
            st.divider()
            
            # Navigation
            st.subheader("Navigation")
            
            # Use session state to track current page
            if st.button("📋 Documents", use_container_width=True, 
                        type="primary" if st.session_state.current_page == "documents" else "secondary"):
                st.session_state.current_page = "documents"
                st.rerun()
            
            if st.button("⬆️ Upload", use_container_width=True,
                        type="primary" if st.session_state.current_page == "upload" else "secondary"):
                st.session_state.current_page = "upload"
                st.rerun()
            
            if st.button("👥 Share", use_container_width=True,
                        type="primary" if st.session_state.current_page == "share" else "secondary"):
                st.session_state.current_page = "share"
                st.rerun()
            
            if st.button("📊 Audit Logs", use_container_width=True,
                        type="primary" if st.session_state.current_page == "audit" else "secondary"):
                st.session_state.current_page = "audit"
                st.rerun()
            
            # Admin-only navigation
            if user_info.role == "Admin":
                st.divider()
                if st.button("🔧 Admin Panel", use_container_width=True,
                            type="primary" if st.session_state.current_page == "admin" else "secondary"):
                    st.session_state.current_page = "admin"
                    st.rerun()
            
            st.divider()
            
            # Sign out button
            if st.button("🚪 Sign Out", use_container_width=True):
                with st.spinner("Signing out..."):
                    if st.session_state.tokens:
                        st.session_state.auth_client.sign_out(st.session_state.tokens.access_token)
                    clear_session()
                    st.rerun()
            
            return st.session_state.current_page
        
        return "documents"


def show_documents_page():
    """Display documents list page"""
    from pages import _documents as documents
    documents.show()


def show_upload_page():
    """Display upload page"""
    from pages import upload
    upload.show()


def show_share_page():
    """Display share page"""
    from pages import share
    share.show()


def show_audit_page():
    """Display audit logs page"""
    from pages import audit
    audit.show()


def show_admin_page():
    """Display admin page"""
    from pages import admin
    admin.show()


def main():
    """Main application entry point"""
    
    # Check authentication
    if not is_authenticated():
        show_login_page()
        return
    
    # Show sidebar and get selected page
    page = show_sidebar()
    
    # Route to appropriate page with error handling
    try:
        if page == "documents":
            show_documents_page()
        elif page == "upload":
            show_upload_page()
        elif page == "share":
            show_share_page()
        elif page == "audit":
            show_audit_page()
        elif page == "admin":
            show_admin_page()
        else:
            show_documents_page()
    except Exception as e:
        st.error(f"❌ Error loading page: {str(e)}")
        st.exception(e)  # Show full traceback for debugging


if __name__ == "__main__":
    main()

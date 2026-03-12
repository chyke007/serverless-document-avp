"""Documents list page"""

import streamlit as st
import sys
import os

# Add parent directory to path to import from app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import get_bearer_token, get_user_info, init_session_state, is_authenticated
from config import AppConfig
from api_client import APIClient
from datetime import datetime
import webbrowser

# Initialize session state if not already done
# This runs when the module is imported/executed
if 'initialized' not in st.session_state:
    try:
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
    except Exception as e:
        # If initialization fails, log but don't crash
        st.error(f"Initialization error: {str(e)}")
        st.session_state.initialized = False


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def format_timestamp(timestamp_str: str) -> str:
    """Format ISO timestamp to readable format"""
    try:
        dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return timestamp_str


def show():
    """Display documents list page"""
    # Check authentication when accessed as standalone page
    if not is_authenticated():
        st.error("Please sign in to access documents")
        st.info("Go to the main app page to sign in")
        return
    
    st.title("📋 My Documents")
    
    # Ensure API client is initialized
    if 'api_client' not in st.session_state:
        config = st.session_state.get('config')
        if not config:
            from config import AppConfig
            config = AppConfig.from_env()
            st.session_state.config = config
        st.session_state.api_client = APIClient(base_url=config.api_gateway_url)
    
    access_token = get_bearer_token()
    user_info = get_user_info()
    api_client = st.session_state.api_client
    
    if not access_token or not user_info:
        st.error("Authentication required")
        st.info("Please sign in to access documents")
        return
    
    # Filters
    col1, col2, col3 = st.columns([2, 2, 1])
    
    with col1:
        search_query = st.text_input("🔍 Search", placeholder="Search by filename...")
    
    with col2:
        filter_owner = st.selectbox(
            "Filter by owner",
            ["All documents", "My documents", "Shared with me"]
        )
    
    with col3:
        st.write("")  # Spacing
        st.write("")  # Spacing
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()
    
    st.divider()
    
    # Build filters
    filters = {}
    if filter_owner == "My documents":
        filters['owner'] = user_info.user_id
    elif filter_owner == "Shared with me":
        filters['shared_with'] = user_info.user_id
    
    # Load documents
    with st.spinner("Loading documents..."):
        success, list_response, error = api_client.list_documents(
            access_token=access_token,
            filters=filters
        )
    
    if not success:
        error_msg = error or "Unknown error"
        st.error(f"❌ Error loading documents: {error_msg}")
        # Show full error in expander for debugging
        with st.expander("🔍 Error Details (Click to expand)"):
            st.code(f"{error_msg}", language="json")
            st.caption("Check the browser Network tab for the full API response")
        return
    
    if not list_response or not list_response.documents:
        st.info("📭 No documents found")
        st.caption("Upload your first document using the Upload page")
        return
    
    # Filter by search query
    documents = list_response.documents
    if search_query:
        documents = [
            doc for doc in documents
            if search_query.lower() in doc.filename.lower()
        ]
    
    # Display document count
    st.caption(f"Showing {len(documents)} document(s)")
    
    # Display documents in a table-like format
    for doc in documents:
        with st.container():
            col1, col2, col3, col4 = st.columns([3, 2, 2, 2])
            
            with col1:
                st.markdown(f"**📄 {doc.filename}**")
                st.caption(f"ID: {doc.document_id[:8]}...")
            
            with col2:
                st.text(f"Owner: {doc.owner[:20]}...")
                st.caption(f"Size: {format_file_size(doc.size_bytes)}")
            
            with col3:
                st.text(f"Uploaded: {format_timestamp(doc.upload_timestamp)}")
                if doc.department:
                    st.caption(f"Dept: {doc.department}")
            
            with col4:
                # Action buttons
                btn_col1, btn_col2 = st.columns(2)
                
                with btn_col1:
                    if st.button("⬇️", key=f"download_{doc.document_id}", help="Download"):
                        download_document(doc.document_id, doc.filename)
                
                with btn_col2:
                    # Show delete button if:
                    # - owner (any role), OR
                    # - Admin, OR
                    # - Manager for documents in their department
                    can_delete = (
                        doc.owner == user_info.user_id
                        or user_info.role == "Admin"
                        or (
                            user_info.role == "Manager"
                            and user_info.department
                            and doc.department
                            and user_info.department == doc.department
                        )
                    )

                    if can_delete:
                        delete_key = f"show_delete_{doc.document_id}"
                        if st.button("🗑️", key=f"delete_{doc.document_id}", help="Delete"):
                            st.session_state[delete_key] = True
                            st.rerun()
                        
                        # Show confirmation form if delete was clicked
                        if st.session_state.get(delete_key, False):
                            delete_document(doc.document_id, doc.filename, delete_key)
            
            st.divider()
    
    # Pagination
    if list_response.next_token:
        if st.button("Load More"):
            st.info("Pagination not yet implemented")


# Note: This page is called by app.py via show_documents_page()
# If accessed directly via /documents URL, it will be blank - use the sidebar navigation instead


def download_document(document_id: str, filename: str):
    """Handle document download"""
    access_token = get_bearer_token()
    api_client = st.session_state.api_client
    
    with st.spinner(f"Preparing download for {filename}..."):
        success, download_response, error = api_client.download_document(
            access_token=access_token,
            document_id=document_id
        )
    
    if success and download_response:
        st.success(f"✅ Download ready for {filename}")
        st.markdown(f"[Click here to download]({download_response.presigned_url})")
        st.caption(f"Link expires at: {format_timestamp(download_response.expires_at)}")
    else:
        st.error(f"❌ Download failed: {error}")


def delete_document(document_id: str, filename: str, delete_key: str):
    """Handle document deletion with confirmation"""
    confirm_key = f"delete_confirm_{document_id}"
    
    # Show confirmation dialog (not in a form, use regular buttons)
    st.warning(f"⚠️ Are you sure you want to delete **{filename}**?")
    st.caption("This action cannot be undone.")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("✅ Yes, Delete", key=f"{confirm_key}_yes", use_container_width=True):
            access_token = get_bearer_token()
            api_client = st.session_state.api_client
            
            with st.spinner(f"Deleting {filename}..."):
                success, error = api_client.delete_document(
                    access_token=access_token,
                    document_id=document_id
                )
            
            # Clear the delete state
            if delete_key in st.session_state:
                del st.session_state[delete_key]
            
            if success:
                st.success(f"✅ {filename} deleted successfully")
                st.rerun()
            else:
                st.error(f"❌ Delete failed: {error}")
                # Keep the form open if deletion failed
                st.session_state[delete_key] = True
    
    with col2:
        if st.button("❌ Cancel", key=f"{confirm_key}_cancel", use_container_width=True):
            # Clear the delete state
            if delete_key in st.session_state:
                del st.session_state[delete_key]
            st.rerun()

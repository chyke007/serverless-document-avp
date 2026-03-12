"""Document upload page"""

import streamlit as st
import sys
import os

# Add parent directory to path to import from app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import get_bearer_token, get_user_info, init_session_state, is_authenticated
from config import AppConfig
from api_client import APIClient

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
    """Display document upload page"""
    # Check authentication when accessed as standalone page
    if not is_authenticated():
        st.error("Please sign in to upload documents")
        st.info("Go to the main app page to sign in")
        return
    
    st.title("⬆️ Upload Document")
    
    access_token = get_bearer_token()
    user_info = get_user_info()
    api_client = st.session_state.api_client
    
    if not access_token or not user_info:
        st.error("Authentication required")
        return
    
    st.write("Upload a new document to the system")
    
    with st.form("upload_form", clear_on_submit=True):
        # File picker
        uploaded_file = st.file_uploader(
            "Choose a file",
            type=None,  # Allow all file types
            help="Maximum file size: 5GB"
        )
        
        # Metadata inputs
        st.subheader("Document Metadata")
        
        col1, col2 = st.columns(2)
        
        with col1:
            department = st.text_input(
                "Department",
                value=user_info.department if user_info.department else "",
                help="Department this document belongs to"
            )
        
        with col2:
            tags_input = st.text_input(
                "Tags",
                placeholder="tag1, tag2, tag3",
                help="Comma-separated tags"
            )
        
        description = st.text_area(
            "Description (optional)",
            placeholder="Brief description of the document"
        )
        
        # Submit button
        submit = st.form_submit_button("📤 Upload Document", use_container_width=True)
        
        if submit:
            if not uploaded_file:
                st.error("❌ Please select a file to upload")
            else:
                # Parse tags
                tags = [tag.strip() for tag in tags_input.split(',') if tag.strip()]
                
                # Prepare metadata
                metadata = {
                    'department': department,
                    'tags': tags
                }
                if description:
                    metadata['description'] = description
                
                # Check file size (5GB limit)
                file_size = uploaded_file.size
                max_size = 5 * 1024 * 1024 * 1024  # 5GB
                
                if file_size > max_size:
                    st.error(f"❌ File too large: {format_file_size(file_size)}. Maximum size is 5GB")
                else:
                    upload_document(
                        uploaded_file=uploaded_file,
                        metadata=metadata,
                        access_token=access_token,
                        api_client=api_client
                    )
    
    # Upload tips
    st.divider()
    st.subheader("📝 Upload Tips")
    st.markdown("""
    - Maximum file size: **5GB**
    - All file types are supported
    - Files are encrypted at rest in S3
    - Upload links expire after 15 minutes
    - You can track upload progress in real-time
    """)


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def upload_document(uploaded_file, metadata: dict, access_token: str, api_client):
    """Handle document upload process"""
    
    filename = uploaded_file.name
    content_type = uploaded_file.type or 'application/octet-stream'
    file_size = uploaded_file.size
    
    # Step 1: Get pre-signed URL
    with st.spinner(f"Preparing upload for {filename}..."):
        success, upload_response, error = api_client.upload_document(
            access_token=access_token,
            filename=filename,
            content_type=content_type,
            metadata=metadata
        )
    
    if not success:
        # Show detailed error for debugging
        error_msg = error or "Unknown error"
        st.error(f"❌ Upload preparation failed: {error_msg}")
        # Show full error in expander for debugging
        with st.expander("🔍 Error Details (Click to expand)"):
            st.code(f"{error_msg}", language="json")
            st.caption("Check the browser Network tab for the full API response")
        return
    
    st.info(f"📋 Document ID: {upload_response.document_id}")
    
    # Step 2: Upload to S3
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    status_text.text("Uploading to S3...")
    progress_bar.progress(30)
    
    # Read file content
    file_content = uploaded_file.read()
    
    progress_bar.progress(50)
    
    # Upload to S3
    success, error = api_client.upload_to_s3(
        presigned_url=upload_response.presigned_url,
        file_content=file_content,
        content_type=content_type
    )
    
    if not success:
        progress_bar.progress(100)
        status_text.text("")
        error_msg = error or "Unknown error"
        st.error(f"❌ S3 upload failed: {error_msg}")
        # Show detailed error for debugging
        with st.expander("🔍 S3 Upload Error Details"):
            st.code(f"{error_msg}", language="text")
            st.caption("Common causes: Content-Type mismatch, expired URL, or CORS issue")
        return
    
    progress_bar.progress(100)
    status_text.text("Upload complete!")
    
    # Success message
    st.success(f"✅ {filename} uploaded successfully!")
    st.balloons()
    
    # Display upload details
    with st.expander("Upload Details"):
        st.write(f"**Filename:** {filename}")
        st.write(f"**Document ID:** {upload_response.document_id}")
        st.write(f"**Size:** {format_file_size(file_size)}")
        st.write(f"**Content Type:** {content_type}")
        if metadata.get('department'):
            st.write(f"**Department:** {metadata['department']}")
        if metadata.get('tags'):
            st.write(f"**Tags:** {', '.join(metadata['tags'])}")
    
    # Link to documents page
    st.info("📋 Go to Documents page to view your uploaded file")


# When accessed as a standalone Streamlit page, automatically call show()
# Only call show() if not being imported by app.py (which calls show() itself)
if 'current_page' not in st.session_state or st.session_state.get('current_page') != 'upload':
    # This page is being accessed directly via Streamlit navigation
    show()

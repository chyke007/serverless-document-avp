"""Document sharing page"""

import streamlit as st
import sys
import os
from typing import Optional

# Add parent directory to path to import from app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import get_bearer_token, get_user_info, init_session_state, is_authenticated, get_user_sub_from_email
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
    """Display document sharing page"""
    # Check authentication when accessed as standalone page
    if not is_authenticated():
        st.error("Please sign in to share documents")
        st.info("Go to the main app page to sign in")
        return
    
    st.title("👥 Share Documents")
    
    access_token = get_bearer_token()
    user_info = get_user_info()
    api_client = st.session_state.api_client
    
    if not access_token or not user_info:
        st.error("Authentication required")
        return
    
    st.write("Share documents with other users and manage permissions")
    
    # Load documents the user can manage/share
    # - Admin: all documents
    # - Manager: all documents in their department (matches Cedar policy)
    # - Others: only documents they own
    if user_info.role == "Admin":
        list_filters = {}
    elif user_info.role == "Manager" and user_info.department:
        list_filters = {"department": user_info.department}
    else:
        list_filters = {"owner": user_info.user_id}

    with st.spinner("Loading your documents..."):
        success, list_response, error = api_client.list_documents(
            access_token=access_token,
            filters=list_filters
        )
    
    if not success:
        st.error(f"❌ Error loading documents: {error}")
        return
    
    if not list_response or not list_response.documents:
        st.info("📭 You don't have any documents to share")
        st.caption("Upload documents first using the Upload page")
        return
    
    # Document selection
    documents = list_response.documents
    document_options = {f"{doc.filename} ({doc.document_id[:8]}...)": doc.document_id for doc in documents}
    
    with st.form("share_form"):
        st.subheader("Share Document")
        
        # Select document
        selected_doc_label = st.selectbox(
            "Select document to share",
            options=list(document_options.keys())
        )
        document_id = document_options[selected_doc_label]
        
        # User to share with
        target_user = st.text_input(
            "User ID (UUID) or Email",
            placeholder="12345678-1234-1234-1234-123456789012 or user@example.com",
            help="Enter the Cognito user ID (UUID/sub) or email address. If email is provided, it will be resolved to the user's sub."
        )
        
        # Permissions
        st.write("**Permissions to grant:**")
        
        col1, col2 = st.columns(2)
        
        with col1:
            perm_read = st.checkbox("📖 Read", value=True, help="Allow user to view and download the document")
            perm_write = st.checkbox("✏️ Write", help="Allow user to modify the document")
        
        with col2:
            perm_delete = st.checkbox("🗑️ Delete", help="Allow user to delete the document")
            perm_share = st.checkbox("👥 Share", help="Allow user to share the document with others")
        
        # Submit button
        submit = st.form_submit_button("🔗 Share Document", use_container_width=True)
        
        if submit:
            if not target_user:
                st.error("❌ Please enter a user ID or email")
            else:
                # Build permissions list
                permissions = []
                if perm_read:
                    permissions.append("read")
                if perm_write:
                    permissions.append("write")
                if perm_delete:
                    permissions.append("delete")
                if perm_share:
                    permissions.append("share")
                
                if not permissions:
                    st.error("❌ Please select at least one permission")
                else:
                    # Resolve email to sub (UUID) if needed
                    target_user_id = target_user
                    
                    # Check if input looks like an email (contains @)
                    if '@' in target_user:
                        config = st.session_state.get('config')
                        if config:
                            with st.spinner(f"Resolving email to user ID..."):
                                resolved_sub = get_user_sub_from_email(
                                    target_user,
                                    config.cognito_user_pool_id,
                                    config.cognito_region
                                )
                                
                                if resolved_sub:
                                    target_user_id = resolved_sub
                                    st.info(f"✅ Resolved {target_user} to user ID: {resolved_sub[:8]}...")
                                else:
                                    st.error(f"❌ Could not find user with email: {target_user}")
                                    st.info("Please verify the email address or use the user's UUID directly")
                                    return
                        else:
                            st.error("❌ Configuration not available. Cannot resolve email to user ID.")
                            return
                    
                    share_document(
                        document_id=document_id,
                        target_user=target_user_id,
                        permissions=permissions,
                        access_token=access_token,
                        api_client=api_client,
                        original_input=target_user if '@' in target_user else None
                    )
    
    # Display shared documents
    st.divider()
    st.subheader("📊 Shared Documents")
    
    # Show documents that have been shared
    shared_docs = [doc for doc in documents if doc.shared_with]
    
    if shared_docs:
        for doc in shared_docs:
            with st.expander(f"📄 {doc.filename}"):
                st.write(f"**Document ID:** {doc.document_id}")
                st.write(f"**Shared with {len(doc.shared_with)} user(s):**")
                for user_id in doc.shared_with:
                    st.caption(f"  • {user_id}")
    else:
        st.info("No documents have been shared yet")
    
    # Sharing tips
    st.divider()
    st.subheader("💡 Sharing Tips")
    st.markdown("""
    - **Read**: User can view and download the document
    - **Write**: User can modify document metadata
    - **Delete**: User can delete the document
    - **Share**: User can share the document with others
    - Permissions are enforced by Cedar policies
    - Changes take effect immediately
    """)


def share_document(document_id: str, target_user: str, permissions: list, access_token: str, api_client, original_input: Optional[str] = None):
    """Handle document sharing"""
    
    display_name = original_input if original_input else target_user
    
    with st.spinner(f"Sharing document with {display_name}..."):
        success, error = api_client.share_document(
            access_token=access_token,
            document_id=document_id,
            user_id=target_user,  # Use resolved sub (UUID)
            permissions=permissions
        )
    
    if success:
        st.success(f"✅ Document shared successfully with {display_name}")
        st.info(f"Granted permissions: {', '.join(permissions)}")
        
        # Show success details
        with st.expander("Sharing Details"):
            st.write(f"**Document ID:** {document_id}")
            st.write(f"**Shared with:** {display_name}")
            if original_input and original_input != target_user:
                st.write(f"**User ID (sub):** {target_user}")
            st.write(f"**Permissions:** {', '.join(permissions)}")
            st.caption("The user can now access this document with the granted permissions")
    else:
        st.error(f"❌ Sharing failed: {error}")
        
        # Show troubleshooting tips
        with st.expander("Troubleshooting"):
            st.markdown("""
            **Common issues:**
            - User ID (UUID) or email doesn't exist in the system
            - You don't have permission to share this document
            - Invalid permissions specified
            - Network or service error
            
            **Note:** When using email, ensure the user exists in Cognito.
            For better reliability, use the user's Cognito sub (UUID) directly.
            """)


# When accessed as a standalone Streamlit page, automatically call show()
# Only call show() if not being imported by app.py (which calls show() itself)
if 'current_page' not in st.session_state or st.session_state.get('current_page') != 'share':
    # This page is being accessed directly via Streamlit navigation
    show()

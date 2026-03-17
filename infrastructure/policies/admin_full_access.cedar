// Cedar Policy: Admin Full Access
// Admin users have full access to all documents for all operations

permit(
    principal,
    action in [
        DocumentManagement::Action::"read",
        DocumentManagement::Action::"write",
        DocumentManagement::Action::"delete",
        DocumentManagement::Action::"share"
    ],
    resource
)
when {
    principal has role &&
    principal.role == "Admin"
};

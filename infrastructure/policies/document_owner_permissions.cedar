// Cedar Policy: Document Owner Permissions
// Document owners can always manage their own documents regardless of role

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
    principal has userId &&
    resource has owner &&
    resource.owner == principal.userId
};

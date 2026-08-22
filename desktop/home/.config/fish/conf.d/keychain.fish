# Run only in an interactive terminal, never during a graphical-session startup.
if status is-interactive; and type -q keychain; and test -f ~/.ssh/gitlab_bluma
    keychain --quiet --eval ~/.ssh/gitlab_bluma | source
end

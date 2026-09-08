-- Workspace rules wiki https://wiki.hypr.land/Configuring/Basics/Workspace-Rules/
-- Add your workspace rules here. Increment the workspace number as you go. Do not have duplicate workspaces.
hl.workspace_rule({ workspace = "name:gaming", monitor = PRIMARY_MONITOR })

-- Main monitor (DP-2): workspaces 1-10
hl.workspace_rule({ workspace = "1", monitor = MONITOR1, default = true, persistent = true })
hl.workspace_rule({ workspace = "2", monitor = MONITOR1, default = true, persistent = true })
hl.workspace_rule({ workspace = "3", monitor = MONITOR1, default = true, persistent = true })
hl.workspace_rule({ workspace = "4", monitor = MONITOR1, default = true, persistent = true })
hl.workspace_rule({ workspace = "5", monitor = MONITOR1, default = true, persistent = true })
hl.workspace_rule({ workspace = "6", monitor = MONITOR1, default = true, persistent = true })
hl.workspace_rule({ workspace = "7", monitor = MONITOR1, default = true, persistent = true })
hl.workspace_rule({ workspace = "8", monitor = MONITOR1, default = true, persistent = true })
hl.workspace_rule({ workspace = "9", monitor = MONITOR1, default = true, persistent = true })
hl.workspace_rule({ workspace = "10", monitor = MONITOR1, default = true, persistent = true })

-- Secondary ultrawide (DP-3): workspaces 11-20
hl.workspace_rule({ workspace = "11", monitor = MONITOR2, default = true, persistent = true })
hl.workspace_rule({ workspace = "12", monitor = MONITOR2, default = true, persistent = true })
hl.workspace_rule({ workspace = "13", monitor = MONITOR2, default = true, persistent = true })
hl.workspace_rule({ workspace = "14", monitor = MONITOR2, default = true, persistent = true })
hl.workspace_rule({ workspace = "15", monitor = MONITOR2, default = true, persistent = true })
hl.workspace_rule({ workspace = "16", monitor = MONITOR2, default = true, persistent = true })
hl.workspace_rule({ workspace = "17", monitor = MONITOR2, default = true, persistent = true })
hl.workspace_rule({ workspace = "18", monitor = MONITOR2, default = true, persistent = true })
hl.workspace_rule({ workspace = "19", monitor = MONITOR2, default = true, persistent = true })
hl.workspace_rule({ workspace = "20", monitor = MONITOR2, default = true, persistent = true })
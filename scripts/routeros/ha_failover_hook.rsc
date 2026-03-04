# MikroTik HA Failover Hook Script
# Called by VRRP on-master / on-backup events to notify the orchestrator.
#
# Usage in VRRP config:
#   /interface vrrp set [find] \
#     on-master="/system script run ha_failover_hook" \
#     on-backup="/system script run ha_failover_hook"
#
# Configuration variables:
#   :global haOrchestratorUrl "http://10.0.0.100:8080"
#   :global haRouterRole "master"   (or "backup")

:global haOrchestratorUrl
:global haRouterRole

:if ([:typeof $haRouterRole] = "nothing") do={
    :global haRouterRole "unknown"
}

# Determine current VRRP state
:local vrrpState "unknown"
:local vrrpMasterCount 0
:local vrrpBackupCount 0

:foreach vrrpIf in=[/interface vrrp find running=yes] do={
    :local isMaster [/interface vrrp get $vrrpIf master]
    :if ($isMaster) do={
        :set vrrpMasterCount ($vrrpMasterCount + 1)
    } else={
        :set vrrpBackupCount ($vrrpBackupCount + 1)
    }
}

:if ($vrrpMasterCount > 0 && $vrrpBackupCount = 0) do={
    :set vrrpState "master"
} else={
    :if ($vrrpBackupCount > 0 && $vrrpMasterCount = 0) do={
        :set vrrpState "backup"
    } else={
        :set vrrpState "mixed"
    }
}

:local identity [/system identity get name]

:log warning ("HA: VRRP state change detected - role=" . $haRouterRole . " vrrp_state=" . $vrrpState)

# Notify orchestrator
:do {
    :local eventUrl ($haOrchestratorUrl . "/api/vrrp-event")
    :local postData ("{\"router\":\"" . $haRouterRole . "\",\"vrrp_state\":\"" . $vrrpState . "\",\"identity\":\"" . $identity . "\"}")
    /tool fetch url=$eventUrl mode=http http-method=post http-data=$postData http-content-type="application/json" as-value output=none
    :log info "HA: Orchestrator notified of VRRP state change"
} on-error={
    :log error "HA: Failed to notify orchestrator of VRRP state change"
}

# Update identity to reflect role
:local baseIdentity $identity
# Remove any existing HA suffix
:if ([:find $identity "_HA_" -1] != nil) do={
    :set baseIdentity [:pick $identity 0 [:find $identity "_HA_"]]
}
:local newIdentity ($baseIdentity . "_HA_" . [:toupper $haRouterRole] . "_" . [:topper $vrrpState])
/system identity set name=$newIdentity
:log info ("HA: Identity updated to " . $newIdentity)

# MikroTik HA Health Check Script
# Deploy on both routers. Checks orchestrator /health endpoint.
# If orchestrator is unreachable for N consecutive checks, lowers VRRP priority.
#
# Configuration variables (set these before running):
#   :global haOrchestratorUrl "http://10.0.0.100:8080/health"
#   :global haFailThreshold 3
#   :global haVrrpPriorityNormal 150    (or 100 for backup)
#   :global haVrrpPriorityDemoted 50
#   :global haCheckInterval "5s"
#
# Schedule this script:
#   /system scheduler add name=ha_health_check interval=5s \
#     on-event="/system script run ha_health_check"

:global haOrchestratorUrl
:global haFailThreshold
:global haVrrpPriorityNormal
:global haVrrpPriorityDemoted
:global haFailCount

# Initialize fail counter
:if ([:typeof $haFailCount] = "nothing") do={
    :global haFailCount 0
}

# Defaults
:if ([:typeof $haFailThreshold] = "nothing") do={
    :global haFailThreshold 3
}
:if ([:typeof $haVrrpPriorityNormal] = "nothing") do={
    :global haVrrpPriorityNormal 150
}
:if ([:typeof $haVrrpPriorityDemoted] = "nothing") do={
    :global haVrrpPriorityDemoted 50
}

:local reachable false

# Check orchestrator health endpoint
:do {
    :local result [/tool fetch url=$haOrchestratorUrl mode=http as-value output=user]
    :if (($result->"status") = "finished") do={
        :set reachable true
    }
} on-error={
    :set reachable false
}

:if ($reachable) do={
    # Orchestrator reachable - reset counter
    :if ($haFailCount > 0) do={
        :global haFailCount 0
        :log info "HA: Orchestrator reachable, resetting fail counter"

        # Restore VRRP priority if it was lowered
        :foreach vrrpIf in=[/interface vrrp find] do={
            :local currentPri [/interface vrrp get $vrrpIf priority]
            :if ($currentPri < $haVrrpPriorityNormal) do={
                /interface vrrp set $vrrpIf priority=$haVrrpPriorityNormal
                :local vrrpName [/interface vrrp get $vrrpIf name]
                :log warning ("HA: Restored VRRP priority to " . $haVrrpPriorityNormal . " on " . $vrrpName)
            }
        }
    }
} else={
    # Orchestrator unreachable - increment counter
    :global haFailCount ($haFailCount + 1)
    :log warning ("HA: Orchestrator unreachable, fail count: " . $haFailCount . "/" . $haFailThreshold)

    :if ($haFailCount >= $haFailThreshold) do={
        # Lower VRRP priority on all VRRP interfaces
        :foreach vrrpIf in=[/interface vrrp find] do={
            :local currentPri [/interface vrrp get $vrrpIf priority]
            :if ($currentPri > $haVrrpPriorityDemoted) do={
                /interface vrrp set $vrrpIf priority=$haVrrpPriorityDemoted
                :local vrrpName [/interface vrrp get $vrrpIf name]
                :log error ("HA: Lowered VRRP priority to " . $haVrrpPriorityDemoted . " on " . $vrrpName . " (orchestrator unreachable)")
            }
        }
    }
}

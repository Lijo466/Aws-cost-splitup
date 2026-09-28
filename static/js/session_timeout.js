/**
 * 30-Minute Inactive Session Timeout System
 * Monitors user interaction (mouse, keyboard, click, scroll).
 * Shows a 2-minute warning modal before automatically logging out at 30 minutes of inactivity.
 */
(function() {
    const INACTIVITY_TIMEOUT_MS = 30 * 60 * 1000; // 30 minutes
    const WARNING_TIMEOUT_MS = 28 * 60 * 1000;    // 28 minutes (2 min warning)

    let warningTimer = null;
    let logoutTimer = null;

    function resetInactivityTimers() {
        clearTimeout(warningTimer);
        clearTimeout(logoutTimer);
        hideWarningModal();

        // Start 28-minute warning timer
        warningTimer = setTimeout(showWarningModal, WARNING_TIMEOUT_MS);
        
        // Start 30-minute logout timer
        logoutTimer = setTimeout(performAutoLogout, INACTIVITY_TIMEOUT_MS);
    }

    function showWarningModal() {
        let modal = document.getElementById('session-warning-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'session-warning-modal';
            modal.style.cssText = 'position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0, 0, 0, 0.85); backdrop-filter: blur(8px); z-index: 10000; display: flex; align-items: center; justify-content: center;';
            modal.innerHTML = `
                <div style="background: #10141d; border: 1px solid rgba(255, 23, 68, 0.4); border-radius: 16px; padding: 2rem; width: 420px; max-width: 90%; text-align: center; box-shadow: 0 16px 48px rgba(0,0,0,0.6);">
                    <div style="font-size: 2.5rem; margin-bottom: 0.5rem;">⏳</div>
                    <h3 style="font-family: 'Outfit', sans-serif; font-size: 1.3rem; margin-bottom: 0.5rem; color: #fff;">Session Expiring Soon</h3>
                    <p style="color: #a0aec0; font-size: 0.9rem; margin-bottom: 1.5rem; line-height: 1.4;">
                        You have been inactive for 28 minutes. Your session will automatically log out in <strong style="color: #ff8a80;">2 minutes</strong> due to inactivity security policy.
                    </p>
                    <button id="btn-extend-session" style="background: #00e5ff; color: #000; font-weight: 700; border: none; padding: 0.75rem 1.5rem; border-radius: 8px; font-size: 0.9rem; cursor: pointer; transition: all 0.2s;">
                        Stay Logged In
                    </button>
                </div>
            `;
            document.body.appendChild(modal);
            document.getElementById('btn-extend-session').onclick = () => {
                fetch('/api/sync_check').catch(() => {});
                resetInactivityTimers();
            };
        }
        modal.style.display = 'flex';
    }

    function hideWarningModal() {
        const modal = document.getElementById('session-warning-modal');
        if (modal) modal.style.display = 'none';
    }

    function performAutoLogout() {
        sessionStorage.clear();
        localStorage.clear();
        window.location.href = '/logout?reason=timeout';
    }

    // User activity listeners
    const activityEvents = ['mousemove', 'keydown', 'mousedown', 'touchstart', 'scroll', 'click'];
    let lastResetTime = Date.now();

    function onUserActivity() {
        const now = Date.now();
        if (now - lastResetTime > 5000) { // Throttle resets to once every 5 seconds
            lastResetTime = now;
            resetInactivityTimers();
        }
    }

    window.addEventListener('DOMContentLoaded', () => {
        activityEvents.forEach(evt => window.addEventListener(evt, onUserActivity, { passive: true }));
        resetInactivityTimers();
    });
})();

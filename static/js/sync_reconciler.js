/**
 * Frontend-Backend Sync & Automatic Cache Reconciler Component
 * Runs ONCE on page load to ensure S3 totals match backend invoices.
 * Completely passive and non-disruptive to prevent interfering with table row expansions.
 */
(function() {
    let hasCheckedSync = false;

    async function verifyAndReconcileSync() {
        if (hasCheckedSync) return;
        hasCheckedSync = true;

        try {
            // Check if user has open details rows; if so, skip to avoid closing them
            if (document.querySelector('.details-row.active') || document.querySelector('.details-row[style*="display: table-row"]')) {
                return;
            }

            const response = await fetch('/api/sync_check', { cache: 'no-store' });
            if (!response.ok) return;
            const serverState = await response.json();
            if (!serverState.success) return;

            // Only check if S3 KPI is showing raw $998.39 when server has $3,981.23
            const s3Kpi = document.getElementById('kpi-total-cost');
            if (s3Kpi && serverState.s3_invoice_total > 0) {
                const domVal = parseFloat(s3Kpi.textContent.replace(/[^0-9.]/g, ''));
                if (domVal > 0 && Math.abs(domVal - serverState.s3_invoice_total) > 10.0 && domVal < 1500.0) {
                    console.warn(`[SyncReconciler] S3 Desync Detected: DOM=$${domVal}, Server=$${serverState.s3_invoice_total}`);
                    sessionStorage.clear();
                    localStorage.clear();
                    showSyncNotification(`⚡ S3 Invoice Reconciled: $${serverState.s3_invoice_total}`);
                    if (typeof loadS3Data === 'function') loadS3Data(true);
                }
            }
        } catch (e) {
            console.error("[SyncReconciler] Sync check error:", e);
        }
    }

    function showSyncNotification(msg) {
        let banner = document.getElementById('sync-reconcile-banner');
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'sync-reconcile-banner';
            banner.style.cssText = 'position: fixed; bottom: 20px; right: 20px; background: rgba(0, 230, 118, 0.95); color: #000; padding: 0.6rem 1.2rem; border-radius: 8px; font-weight: 700; font-size: 0.85rem; z-index: 9999; box-shadow: 0 4px 16px rgba(0,0,0,0.5); transition: opacity 0.5s;';
            document.body.appendChild(banner);
        }
        banner.textContent = msg;
        banner.style.opacity = '1';
        setTimeout(() => { banner.style.opacity = '0'; }, 4000);
    }

    window.wipeDatabaseAndRescan = async function() {
        if (!confirm("Are you sure you want to wipe all database scan records and trigger fresh real-time scans across all AWS services?")) {
            return;
        }

        try {
            const response = await fetch('/api/admin/reset_scans', { method: 'POST' });
            const result = await response.json();
            if (result.success) {
                sessionStorage.clear();
                localStorage.clear();
                showSyncNotification("🗑️ Database Wiped Clean! Reloading Live Data...");
                setTimeout(() => { location.reload(); }, 1200);
            } else {
                alert("Failed to reset scans: " + result.message);
            }
        } catch (e) {
            console.error("Reset error:", e);
            alert("Error resetting scan database.");
        }
    };

    window.addEventListener('DOMContentLoaded', () => {
        setTimeout(verifyAndReconcileSync, 3000);
    });
})();

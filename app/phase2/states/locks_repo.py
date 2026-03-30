"""Locks Repository: Lease-based distributed locking

Handles workflow_state_locks operations.
Ensures single executor per session via lease-based locks.
"""

from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from uuid import UUID, uuid4
import logging

logger = logging.getLogger(__name__)


class LockInfo:
    """Information about an acquired lock."""

    def __init__(self, lock_token: UUID, locked_until: datetime, session_id: UUID):
        self.lock_token = lock_token
        self.locked_until = locked_until
        self.session_id = session_id

    def is_expired(self) -> bool:
        """Check if lock is expired."""
        return datetime.utcnow() >= self.locked_until

    def time_remaining(self) -> timedelta:
        """Time until lock expires."""
        return self.locked_until - datetime.utcnow()


class LocksRepository:
    """Repository for session locking."""

    def __init__(self, db_client):
        """Initialize with database client.

        Args:
            db_client: Supabase client instance
        """
        self.db = db_client

    async def acquire_lock(
        self,
        session_id: UUID,
        workspace_id: UUID,
        locked_by: str,
        lease_seconds: int = 30,
    ) -> Optional[LockInfo]:
        """Acquire a lease lock for a session.

        Atomically acquires a lock via RPC.
        Returns None if lock is already held (by another worker).

        Args:
            session_id: Session to lock
            workspace_id: Workspace ID
            locked_by: Worker ID / instance identifier
            lease_seconds: How long lock is valid (default 30s)

        Returns:
            LockInfo if acquired, None if lock already held
        """
        try:
            response = self.db.rpc(
                "acquire_workflow_lock",
                {
                    "p_session_id": str(session_id),
                    "p_workspace_id": str(workspace_id),
                    "p_locked_by": locked_by,
                    "p_lease_seconds": lease_seconds,
                },
            ).execute()

            # If response is empty, lock is held by someone else
            if not response.data or len(response.data) == 0:
                logger.warning(f"⚠️ Lock held for {session_id}, cannot acquire")
                return None

            lock_data = response.data[0]
            lock_info = LockInfo(
                lock_token=UUID(lock_data["lock_token"]),
                locked_until=datetime.fromisoformat(lock_data["locked_until"]),
                session_id=session_id,
            )

            logger.info(
                f"🔒 Acquired lock for {session_id} (expires in {lease_seconds}s)"
            )
            return lock_info

        except Exception as e:
            logger.error(f"❌ Failed to acquire lock for {session_id}: {e}")
            return None

    async def renew_lock(
        self,
        session_id: UUID,
        lock_token: UUID,
        lease_seconds: int = 30,
    ) -> bool:
        """Renew an existing lock (heartbeat).

        Called periodically to keep lock alive during execution.
        Returns False if lock is no longer owned by this token.

        Args:
            session_id: Session to renew
            lock_token: Lock token to verify ownership
            lease_seconds: New lease duration

        Returns:
            True if renewed, False if token mismatch or expired
        """
        try:
            response = self.db.rpc(
                "renew_workflow_lock",
                {
                    "p_session_id": str(session_id),
                    "p_lock_token": str(lock_token),
                    "p_lease_seconds": lease_seconds,
                },
            ).execute()

            # Response is a boolean wrapped in data
            success = response.data[0] if response.data else False

            if success:
                logger.debug(f"♻️ Renewed lock for {session_id}")
                return True
            else:
                logger.warning(
                    f"⚠️ Failed to renew lock for {session_id} (token mismatch?)"
                )
                return False

        except Exception as e:
            logger.error(f"❌ Error renewing lock for {session_id}: {e}")
            return False

    async def release_lock(
        self,
        session_id: UUID,
        lock_token: UUID,
    ) -> bool:
        """Release a held lock.

        Should be called when session completes, fails, or is cancelled.

        Args:
            session_id: Session to unlock
            lock_token: Lock token to verify ownership

        Returns:
            True if released, False if token mismatch
        """
        try:
            response = self.db.rpc(
                "release_workflow_lock",
                {
                    "p_session_id": str(session_id),
                    "p_lock_token": str(lock_token),
                },
            ).execute()

            success = response.data[0] if response.data else False

            if success:
                logger.info(f"🔓 Released lock for {session_id}")
                return True
            else:
                logger.warning(
                    f"⚠️ Failed to release lock for {session_id} (token mismatch?)"
                )
                return False

        except Exception as e:
            logger.error(f"❌ Error releasing lock for {session_id}: {e}")
            return False

    async def check_lock_status(self, session_id: UUID) -> Optional[Dict[str, Any]]:
        """Check current lock status (for diagnostics).

        Args:
            session_id: Session to check

        Returns:
            Lock dict or None if no lock
        """
        try:
            response = (
                self.db.table("workflow_state_locks")
                .select("*")
                .eq("session_id", str(session_id))
                .execute()
            )

            if response.data:
                lock = response.data[0]
                lock["is_expired"] = (
                    datetime.fromisoformat(lock["locked_until"]) <= datetime.utcnow()
                )
                return lock
            return None

        except Exception as e:
            logger.error(f"❌ Failed to check lock for {session_id}: {e}")
            return None

    async def force_release_expired_locks(self, workspace_id: UUID) -> int:
        """Force release all expired locks in workspace (maintenance).

        Use cautiously - only if you're sure workers are dead.

        Args:
            workspace_id: Workspace to cleanup

        Returns:
            Number of locks released
        """
        try:
            now = datetime.utcnow().isoformat()
            response = (
                self.db.table("workflow_state_locks")
                .delete()
                .eq("workspace_id", str(workspace_id))
                .lt("locked_until", now)
                .execute()
            )

            count = len(response.data) if response.data else 0
            logger.info(f"🧹 Forced release of {count} expired locks in {workspace_id}")
            return count

        except Exception as e:
            logger.error(f"❌ Error forcing lock release in {workspace_id}: {e}")
            return 0

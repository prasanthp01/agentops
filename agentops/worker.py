import logging
import threading
import time

from .engine import Engine
from .store import LeaseLost, StoreError

log = logging.getLogger("agentops.worker")


class Workers:
    def __init__(self, store):
        self.store, self.stop = store, threading.Event()
        self.threads = []

    def start(self):
        for i in range(self.store.settings.workers):
            thread = threading.Thread(target=self.loop, name=f"agentops-worker-{i}", daemon=True)
            thread.start()
            self.threads.append(thread)

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=5)

    def loop(self):
        next_cleanup = 0
        while not self.stop.is_set():
            try:
                if time.monotonic() > next_cleanup:
                    self.store.cleanup()
                    next_cleanup = time.monotonic() + 60
                job = self.store.claim()
                if job:
                    try:
                        if time.time() - job["created"] > self.store.settings.max_run_seconds:
                            from .api_types import failure_result

                            self.store.finish(
                                job["id"], job["claim"], failure_result(job["id"], "RUN_TIMEOUT")
                            )
                        else:
                            Engine(self.store).execute(job)
                    except LeaseLost:
                        pass
                    except StoreError as exc:
                        from .api_types import failure_result

                        try:
                            self.store.finish(
                                job["id"],
                                job["claim"],
                                failure_result(
                                    job["id"],
                                    "RUN_TIMEOUT" if exc.code == "RUN_TIMEOUT" else "INTERNAL_ERROR",
                                ),
                            )
                        except LeaseLost:
                            pass
                    except Exception:
                        # No request data, credentials or exception strings in logs.
                        log.error("worker_execution_failed")
                        from .api_types import failure_result

                        try:
                            self.store.finish(
                                job["id"], job["claim"], failure_result(job["id"], "INTERNAL_ERROR")
                            )
                        except LeaseLost:
                            pass
                else:
                    self.stop.wait(0.05)
            except Exception:
                log.error("worker_storage_unavailable")
                self.stop.wait(0.5)

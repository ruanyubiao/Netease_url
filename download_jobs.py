"""后台下载任务管理

前端只负责发起任务与轮询状态；真正写入 downloads 在后台线程完成，
页面关闭不影响已启动的任务（服务进程需仍在运行）。
"""

from pathlib import Path
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from music_downloader import MusicDownloader, DownloadException


@dataclass
class SongTaskState:
    music_id: int
    status: str = "pending"  # pending / downloading / done / error / cancelled / skipped
    message: str = ""
    filename: str = ""
    name: str = ""
    artists: str = ""
    file_path: str = ""
    downloaded_bytes: int = 0
    total_bytes: int = 0


@dataclass
class DownloadJob:
    job_id: str
    quality: str
    tasks: List[SongTaskState] = field(default_factory=list)
    status: str = "queued"  # queued / running / completed / failed / cancelled
    message: str = ""
    stop_on_error: bool = False
    cancel_requested: bool = False
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        done = sum(1 for t in self.tasks if t.status == "done")
        failed = sum(1 for t in self.tasks if t.status == "error")
        processed = sum(1 for t in self.tasks if t.status in ("done", "error", "skipped"))
        total = len(self.tasks)
        current = next((t for t in self.tasks if t.status == "downloading"), None)
        return {
            "job_id": self.job_id,
            "quality": self.quality,
            "status": self.status,
            "message": self.message,
            "stop_on_error": self.stop_on_error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "progress": {
                "done": done,
                "failed": failed,
                "processed": processed,
                "total": total,
                "current_id": current.music_id if current else None,
                "current_name": current.name if current else "",
                "downloaded_bytes": current.downloaded_bytes if current else 0,
                "total_bytes": current.total_bytes if current else 0,
            },
            "tasks": [asdict(t) for t in self.tasks],
        }


class DownloadJobManager:
    """单线程顺序下载任务管理器。"""

    def __init__(self, downloader: MusicDownloader):
        self.downloader = downloader
        self._jobs: Dict[str, DownloadJob] = {}
        self._lock = threading.Lock()

    def create_job(
        self,
        music_ids: List[int],
        quality: str = "jymaster",
        stop_on_error: bool = False,
    ) -> DownloadJob:
        if not music_ids:
            raise ValueError("music_ids 不能为空")

        # 去重但保序
        seen = set()
        ordered_ids: List[int] = []
        for mid in music_ids:
            if mid in seen:
                continue
            seen.add(mid)
            ordered_ids.append(mid)

        job = DownloadJob(
            job_id=uuid.uuid4().hex,
            quality=quality,
            tasks=[SongTaskState(music_id=mid) for mid in ordered_ids],
            stop_on_error=stop_on_error,
        )
        with self._lock:
            self._jobs[job.job_id] = job

        thread = threading.Thread(
            target=self._run_job,
            args=(job.job_id,),
            name=f"download-job-{job.job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job

    def get_job(self, job_id: str) -> Optional[DownloadJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel_job(self, job_id: str) -> Optional[DownloadJob]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            job.cancel_requested = True
            if job.status in ("queued", "running"):
                job.message = "正在取消..."
            return job

    def _run_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return

        job.status = "running"
        job.message = "下载中"

        for task in job.tasks:
            if job.cancel_requested:
                task.status = "cancelled"
                task.message = "已取消"
                continue

            if task.status != "pending":
                continue

            task.status = "downloading"
            task.message = "下载中..."
            task.downloaded_bytes = 0
            task.total_bytes = 0

            def on_progress(downloaded: int, total: int, music_info) -> None:
                if music_info:
                    task.name = music_info.name or task.name
                    task.artists = music_info.artists or task.artists
                task.downloaded_bytes = int(downloaded or 0)
                task.total_bytes = int(total or 0)
                if task.total_bytes > 0:
                    pct = int(task.downloaded_bytes * 100 / task.total_bytes)
                    cur_mb = task.downloaded_bytes / (1024 * 1024)
                    all_mb = task.total_bytes / (1024 * 1024)
                    task.message = f"当前下载： {pct}%  {cur_mb:.2f}/{all_mb:.2f} (MB)"
                else:
                    task.message = "下载中..."

            try:
                result = self.downloader.download_music_file(
                    task.music_id, job.quality, progress_callback=on_progress
                )
                if not result.success:
                    raise DownloadException(result.error_message or "下载失败")

                meta = result.music_info
                task.status = "done"
                task.file_path = result.file_path or ""
                task.filename = Path(result.file_path).name if result.file_path else ""
                if meta:
                    task.name = meta.name
                    task.artists = meta.artists
                if result.file_size:
                    task.downloaded_bytes = result.file_size
                    task.total_bytes = result.file_size
                task.message = f"离线下载完成：{task.filename or task.name}"
            except Exception as e:
                task.status = "error"
                err = str(e) or "下载失败"
                task.message = err if err.startswith("下载失败") else f"下载失败：{err}"
                if job.stop_on_error:
                    for rest in job.tasks:
                        if rest.status == "pending":
                            rest.status = "cancelled"
                            rest.message = "因错误停止"
                    job.status = "failed"
                    job.message = task.message
                    job.finished_at = time.time()
                    return

        if job.cancel_requested:
            job.status = "cancelled"
            job.message = "已取消"
        else:
            done = sum(1 for t in job.tasks if t.status == "done")
            failed = sum(1 for t in job.tasks if t.status == "error")
            total = len(job.tasks)
            job.status = "completed"
            if failed:
                job.message = f"下载完成  {done}/{total}，失败 {failed}"
            else:
                job.message = f"下载完成  {total}/{total}"
        job.finished_at = time.time()

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from tiktok_osint.config import get_settings
from tiktok_osint.export.writers import flat_rows, write_csv, write_sqlite, write_xlsx
from tiktok_osint.storage.db import build_repository
from tiktok_osint.worker import run_worker

app = typer.Typer(help="Scanner OSINT hồ sơ TikTok công khai", no_args_is_help=True)


@app.command("init-db")
def init_db_cmd() -> None:
    settings = get_settings()
    build_repository(settings.database_url)
    typer.echo(f"Đã tạo cơ sở dữ liệu {settings.database_url}")


@app.command("import-profiles")
def import_profiles(file: Path, job_name: str = "import") -> None:
    settings = get_settings()
    repo = build_repository(settings.database_url)
    job = repo.create_job(job_name)
    lines = file.read_text(encoding="utf-8").splitlines()
    result = repo.add_targets(str(job["id"]), lines, max_lines=settings.max_import_lines)
    typer.echo(
        f"job={job['id']} accepted={len(result['accepted'])} rejected={len(result['rejected'])} duplicates={len(result['duplicates'])}"
    )


@app.command("enqueue")
def enqueue(job_id: str) -> None:
    from tiktok_osint.queue.redis_queue import build_queue

    settings = get_settings()
    repo = build_repository(settings.database_url)
    repo.set_job_status(job_id, "queued")
    build_queue(settings).push(job_id)
    typer.echo(f"queued {job_id}")


@app.command("worker")
def worker(once: bool = False) -> None:
    asyncio.run(run_worker(once=once))


@app.command("export")
def export(job_id: str, format: str = "csv", out: Path | None = None) -> None:
    settings = get_settings()
    repo = build_repository(settings.database_url)
    suffix = {"csv": ".csv", "xlsx": ".xlsx", "sqlite": ".sqlite"}[format]
    destination = out or (settings.export_dir / f"{job_id}{suffix}")
    if format == "sqlite":
        write_sqlite(destination, repo, job_id)
    else:
        profiles = repo.profiles_for_job(job_id)
        rows = flat_rows(profiles)
        if format == "csv":
            write_csv(destination, rows)
        else:
            write_xlsx(destination, profiles, rows)
    typer.echo(str(destination))


@app.command("serve")
def serve(host: str = "127.0.0.1", port: int = 8088) -> None:
    import uvicorn

    uvicorn.run("tiktok_osint.api.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()

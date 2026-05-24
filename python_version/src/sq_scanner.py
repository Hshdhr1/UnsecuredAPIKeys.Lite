import asyncio
import os
import sys
import logging
import signal
from datetime import datetime, timezone, timedelta

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.align import Align
from rich.live import Live
from rich.layout import Layout
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TaskProgressColumn
from rich import box

# Add parent directory to path to allow imports from python_version.src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from python_version.src.scraper import ScraperBot
from python_version.src.verifier import VerifierBot
from python_version.src.database.models import init_db, get_session_factory, APIKey, ApiStatusEnum, SearchQuery
from sqlalchemy import select, func

# Configuration
FOUND_KEYS_FILE = "found_keys.txt"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///sq_scanner.db")

console = Console()

SQ_BANNER = """
[bold #00F5D4]  ██████  ██████      ███████  ██████  █████  ███    ██ ███    ██ ███████ ██████  [/]
[bold #00F5D4] ██       ██   ██     ██      ██      ██   ██ ████   ██ ████   ██ ██      ██   ██ [/]
[bold #00F5D4]  █████   ██   ██     ███████ ██      ███████ ██ ██  ██ ██ ██  ██ █████   ██████  [/]
[bold #00F5D4]      ██  ██ ▄▄██     \t  ██ ██      ██   ██ ██  ██ ██ ██  ██ ██ ██      ██   ██ [/]
[bold #00F5D4] ██████   ██████  ██  ███████  ██████ ██   ██ ██   ████ ██   ████ ███████ ██   ██ [/]
[bold #00F5D4]             ▀▀                                                                    [/]
"""

COLORS = {
    "primary": "#9D4EDD",
    "secondary": "#7B2CBF",
    "accent": "#C77DFF",
    "success": "#00F5D4",
    "warning": "#FEE440",
    "error": "#F72585",
    "info": "#4CC9F0",
}

class Dashboard:
    def __init__(self):
        self.layout = self.make_layout()
        self.start_time = datetime.now()
        self.scanned = 0
        self.found = 0
        self.errors = 0
        self.logs = []

    def make_layout(self):
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=3)
        )
        layout["body"].split_row(
            Layout(name="main", ratio=3),
            Layout(name="side", ratio=1)
        )
        layout["main"].split_column(
            Layout(name="stats", size=6),
            Layout(name="logs")
        )
        return layout

    def update(self, valid=0, invalid=0):
        runtime = datetime.now() - self.start_time
        runtime_str = str(runtime).split(".")[0]

        # Header
        header_text = Text.from_markup(f"[bold #00F5D4]SQ SCANNER[/] | [white]v2.0.0[/] | [dim]Runtime: {runtime_str}[/]")
        self.layout["header"].update(Panel(Align.center(header_text), border_style="#00F5D4"))

        # Stats
        stats_table = Table.grid(expand=True)
        stats_table.add_column(justify="center", ratio=1)
        stats_table.add_column(justify="center", ratio=1)
        stats_table.add_column(justify="center", ratio=1)
        stats_table.add_column(justify="center", ratio=1)

        stats_table.add_row(
            Panel(f"[bold {COLORS['info']}]{self.scanned}[/]\n[dim]Files/Refs[/]", border_style=COLORS["info"]),
            Panel(f"[bold white]{self.found}[/]\n[dim]Keys Total[/]", border_style="white"),
            Panel(f"[bold {COLORS['success']}]{valid}[/]\n[dim]Valid[/]", border_style=COLORS["success"]),
            Panel(f"[bold {COLORS['error']}]{invalid}[/]\n[dim]Invalid[/]", border_style=COLORS["error"])
        )
        self.layout["stats"].update(stats_table)

        # Logs
        log_text = Text("\n".join(self.logs[-10:]))
        self.layout["logs"].update(Panel(log_text, title="Live Activity", border_style=COLORS["primary"]))

        # Side - Status
        side_panel = Panel(
            Group(
                Text.from_markup(f"[bold {COLORS['success']}]● RUNNING[/]"),
                Text.from_markup(f"\n[dim]Workers:[/]\n 50 threads"),
                Text.from_markup(f"\n[dim]Sources:[/]\n 20+ services")
            ),
            title="Status",
            border_style="#00F5D4"
        )
        self.layout["side"].update(side_panel)

        # Footer
        self.layout["footer"].update(Panel(Align.center(Text("Press Ctrl+C to stop scanning and return to menu", style="dim"))))

    def add_log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{ts}] {msg}")
        if len(self.logs) > 50:
            self.logs.pop(0)

async def start_parsing():
    console.clear()
    console.print("[bold #00F5D4]Инициализация базы данных и поисковых запросов...[/]")
    await init_db(DATABASE_URL)
    session_factory = get_session_factory(DATABASE_URL)

    # Seed default queries if empty
    async with session_factory() as session:
        query_count = await session.scalar(select(func.count()).select_from(SearchQuery))
        if query_count == 0:
            default_patterns = ["sk-", "sk-proj-", "AIza", "gsk_", "pplx-", "xai-", "sk_test_", "ghp_", "glpat-"]
            for pattern in default_patterns:
                session.add(SearchQuery(
                    query=pattern,
                    is_enabled=True,
                    last_search_utc=datetime.now(timezone.utc) - timedelta(days=1)
                ))
            await session.commit()

    scraper = ScraperBot(DATABASE_URL)
    verifier = VerifierBot(DATABASE_URL)
    dashboard = Dashboard()

    last_exported_id = 0
    # Try to find the last exported ID from file if it exists
    if os.path.exists(FOUND_KEYS_FILE):
        try:
            with open(FOUND_KEYS_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
                if lines:
                    last_line = lines[-1]
                    # We don't store ID in the file in the previous version, but let's just export new ones
                    pass
        except: pass

    try:
        with Live(dashboard.layout, refresh_per_second=2, screen=True) as live:
            while True:
                # Update stats from DB
                async with session_factory() as session:
                    total = (await session.execute(select(func.count(APIKey.id)))).scalar()
                    valid = (await session.execute(select(func.count(APIKey.id)).where(APIKey.status == ApiStatusEnum.VALID))).scalar()
                    invalid = (await session.execute(select(func.count(APIKey.id)).where(APIKey.status == ApiStatusEnum.INVALID))).scalar()
                    dashboard.found = total

                    # Export new keys
                    stmt = select(APIKey).where(APIKey.id > last_exported_id).order_by(APIKey.id)
                    new_keys = (await session.execute(stmt)).scalars().all()
                    if new_keys:
                        with open(FOUND_KEYS_FILE, "a", encoding="utf-8") as f:
                            for k in new_keys:
                                f.write(f"{k.first_found_utc} | {k.api_key} | {k.status.name}\n")
                                last_exported_id = max(last_exported_id, k.id)
                        dashboard.add_log(f"Exported {len(new_keys)} new keys to {FOUND_KEYS_FILE}")

                dashboard.update(valid, invalid)

                # Run one scraper cycle
                dashboard.add_log("Starting scraper cycle...")
                await scraper.run_cycle()

                # Run verifier on what we found
                dashboard.add_log("Running verifier...")
                await verifier.run_cycle()

                dashboard.scanned += 1 # Increment cycle count as proxy for activity
                await asyncio.sleep(2)
    except KeyboardInterrupt:
        pass

    console.clear()
    console.print(f"[bold #00F5D4]Парсинг завершен.[/] Всего в базе: [bold]{dashboard.found}[/]")
    console.print(f"Новые ключи экспортированы в [bold]{FOUND_KEYS_FILE}[/]")
    await asyncio.sleep(2)

async def verify_keys():
    console.clear()
    console.print(Align.center(SQ_BANNER))
    console.print("[bold #4CC9F0]Запуск полной перепроверки всех невалидированных ключей...[/]")

    await init_db(DATABASE_URL)
    verifier = VerifierBot(DATABASE_URL)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console
    )

    with progress:
        task = progress.add_task("Верификация...", total=None)
        await verifier.run_cycle()
        progress.update(task, description="Завершено!", completed=100, total=100)

    session_factory = get_session_factory(DATABASE_URL)
    async with session_factory() as session:
        valid_count = (await session.execute(select(func.count(APIKey.id)).where(APIKey.status == ApiStatusEnum.VALID))).scalar()
        invalid_count = (await session.execute(select(func.count(APIKey.id)).where(APIKey.status == ApiStatusEnum.INVALID))).scalar()
        total = (await session.execute(select(func.count(APIKey.id)))).scalar()

    console.print(f"\n[bold #00F5D4]Итоги:[/]")
    console.print(f"💎 Всего найдено: [bold]{total}[/]")
    console.print(f"✅ Валидные: [bold #00F5D4]{valid_count}[/]")
    console.print(f"❌ Невалидные: [bold #F72585]{invalid_count}[/]")

    Prompt.ask("\nНажмите Enter, чтобы вернуться в меню")

async def show_menu():
    while True:
        console.clear()
        console.print(Align.center(SQ_BANNER))

        menu_table = Table(box=None, show_header=False, expand=False)
        menu_table.add_column("Option", style="bold #9D4EDD")
        menu_table.add_column("Description", style="white")

        menu_table.add_row("1.", "Начать парсинг (Start Parsing)")
        menu_table.add_row("2.", "Проверить ключи (Verify Keys)")
        menu_table.add_row("3.", "Выход (Exit)")

        console.print(Align.center(Panel(menu_table, title="[bold white]MAIN MENU[/]", border_style="#00F5D4", padding=(1, 5))))

        # Display current stats in menu
        try:
            await init_db(DATABASE_URL)
            async with get_session_factory(DATABASE_URL)() as session:
                count = (await session.execute(select(func.count(APIKey.id)))).scalar()
                console.print(Align.center(Text(f"Всего ключей в базе: {count}", style="dim")))
        except:
            pass

        choice = Prompt.ask("Выберите", choices=["1", "2", "3"], default="1")

        if choice == "1":
            await start_parsing()
        elif choice == "2":
            await verify_keys()
        elif choice == "3":
            console.print("[bold #F72585]Выход... До встречи![/]")
            break

if __name__ == "__main__":
    # Configure logging to file only to not interfere with Rich
    logging.basicConfig(level=logging.ERROR, filename="sq_scanner.log")

    try:
        asyncio.run(show_menu())
    except KeyboardInterrupt:
        pass

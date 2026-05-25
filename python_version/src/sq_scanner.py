import asyncio
import os
import sys
import logging
import signal
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

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
from rich.columns import Columns
import httpx
from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from python_version.src.scraper import ScraperBot
from python_version.src.verifier import VerifierBot
from python_version.src.database.models import init_db, get_session_factory, APIKey, ApiStatusEnum, SearchQuery, SearchProviderToken, ApiTypeEnum
from sqlalchemy import select, func, delete

# Configuration
load_dotenv()
FOUND_KEYS_TXT = "found_keys.txt"
FOUND_KEYS_JSON = "found_keys.json"
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
        self.valid = 0
        self.invalid = 0
        self.errors = 0
        self.logs = []
        self.recent_keys = []
        self.is_running = True

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
            Layout(name="keys", ratio=2),
            Layout(name="logs", ratio=3)
        )
        return layout

    def update(self):
        runtime = datetime.now() - self.start_time
        runtime_str = str(runtime).split(".")[0]

        # Header
        header_text = Text.from_markup(f"[bold #00F5D4]SQ SCANNER[/] | [white]v2.5.0[/] | [dim]Runtime: {runtime_str}[/]")
        self.layout["header"].update(Panel(Align.center(header_text), border_style="#00F5D4"))

        # Stats
        stats_table = Table.grid(expand=True)
        stats_table.add_column(justify="center", ratio=1)
        stats_table.add_column(justify="center", ratio=1)
        stats_table.add_column(justify="center", ratio=1)
        stats_table.add_column(justify="center", ratio=1)

        stats_table.add_row(
            Panel(f"[bold {COLORS['info']}]{self.scanned}[/]\n[dim]Items Scanned[/]", border_style=COLORS["info"]),
            Panel(f"[bold white]{self.found}[/]\n[dim]Keys Total[/]", border_style="white"),
            Panel(f"[bold {COLORS['success']}]{self.valid}[/]\n[dim]Valid[/]", border_style=COLORS["success"]),
            Panel(f"[bold {COLORS['error']}]{self.invalid}[/]\n[dim]Invalid[/]", border_style=COLORS["error"])
        )
        self.layout["stats"].update(stats_table)

        # Recent Keys Table
        keys_table = Table(title="Последние находки", box=box.SIMPLE, expand=True)
        keys_table.add_column("Время", style="dim")
        keys_table.add_column("Тип", style=COLORS["info"])
        keys_table.add_column("Ключ", style=COLORS["success"])
        keys_table.add_column("Статус", justify="center")

        for k in self.recent_keys[-5:]:
            status_style = COLORS["success"] if k['status'] == "VALID" else COLORS["error"] if k['status'] == "INVALID" else "white"
            keys_table.add_row(k['time'], k['type'], k['key'], f"[{status_style}]{k['status']}[/]")

        self.layout["keys"].update(Panel(keys_table, border_style=COLORS["accent"]))

        # Logs
        log_content = "\n".join(self.logs[-10:])
        self.layout["logs"].update(Panel(Text.from_markup(log_content), title="Live Activity", border_style=COLORS["primary"]))

        # Side - Status
        status_color = COLORS["success"] if self.is_running else COLORS["error"]
        status_text = "● RUNNING" if self.is_running else "● STOPPED"

        side_panel = Panel(
            Group(
                Text.from_markup(f"[bold {status_color}]{status_text}[/]"),
                Text.from_markup(f"\n[dim]Threads:[/]\n 50 active"),
                Text.from_markup(f"\n[dim]Sources:[/]\n 20+ services"),
                Text.from_markup(f"\n[dim]DB URL:[/]\n [dim]{DATABASE_URL.split('/')[-1]}[/]")
            ),
            title="Status",
            border_style="#00F5D4"
        )
        self.layout["side"].update(side_panel)

        # Footer
        self.layout["footer"].update(Panel(Align.center(Text("Press Ctrl+C to return to main menu", style="dim"))))

    def add_log(self, msg, level="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        color = COLORS.get(level, "white")
        clean_msg = msg.replace("[", " ").replace("]", " ")
        self.logs.append(f"[dim]{ts}[/] [bold {color}]{clean_msg}[/]")
        if len(self.logs) > 100:
            self.logs.pop(0)

    def add_key(self, key_obj):
        self.recent_keys.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "type": key_obj.api_type.name if hasattr(key_obj.api_type, 'name') else str(key_obj.api_type),
            "key": f"{key_obj.api_key[:20]}...",
            "status": key_obj.status.name
        })
        if len(self.recent_keys) > 20:
            self.recent_keys.pop(0)

async def start_scanning():
    console.clear()
    await init_db(DATABASE_URL)
    session_factory = get_session_factory(DATABASE_URL)

    dashboard = Dashboard()
    scraper = ScraperBot(DATABASE_URL)
    scraper.on_item_scanned = lambda: setattr(dashboard, 'scanned', dashboard.scanned + 1)
    scraper.on_key_found = lambda k: dashboard.add_key(k)
    verifier = VerifierBot(DATABASE_URL)

    # Background tasks
    async def run_scraper():
        while dashboard.is_running:
            try:
                dashboard.add_log("Starting scraper cycle...", "info")
                await scraper.run_cycle()
                dashboard.add_log("Scraper cycle finished.", "success")
                await asyncio.sleep(60)
            except asyncio.CancelledError: break
            except Exception as e:
                dashboard.add_log(f"Scraper error: {str(e)}", "error")
                await asyncio.sleep(10)

    async def run_verifier():
        while dashboard.is_running:
            try:
                dashboard.add_log("Checking for unverified keys...", "info")
                await verifier.run_cycle()
                await asyncio.sleep(30)
            except asyncio.CancelledError: break
            except Exception as e:
                dashboard.add_log(f"Verifier error: {str(e)}", "error")
                await asyncio.sleep(10)

    async def update_stats():
        last_id = 0
        while dashboard.is_running:
            try:
                async with session_factory() as session:
                    dashboard.found = (await session.execute(select(func.count(APIKey.id)))).scalar()
                    dashboard.valid = (await session.execute(select(func.count(APIKey.id)).where(APIKey.status == ApiStatusEnum.VALID))).scalar()
                    dashboard.invalid = (await session.execute(select(func.count(APIKey.id)).where(APIKey.status == ApiStatusEnum.INVALID))).scalar()

                    # New keys export
                    stmt = select(APIKey).where(APIKey.id > last_id).order_by(APIKey.id)
                    keys = (await session.execute(stmt)).scalars().all()
                    if keys:
                        with open(FOUND_KEYS_TXT, "a", encoding="utf-8") as f:
                            for k in keys:
                                f.write(f"{k.first_found_utc} | {k.api_key} | {k.status.name}\n")
                                last_id = max(last_id, k.id)
                await asyncio.sleep(5)
            except asyncio.CancelledError: break
            except:
                await asyncio.sleep(5)

    tasks = [
        asyncio.create_task(run_scraper()),
        asyncio.create_task(run_verifier()),
        asyncio.create_task(update_stats())
    ]

    try:
        with Live(dashboard.layout, refresh_per_second=4, screen=True) as live:
            while dashboard.is_running:
                dashboard.update()
                await asyncio.sleep(0.5)
    except KeyboardInterrupt:
        dashboard.is_running = False
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

async def view_keys():
    console.clear()
    console.print(Align.center(SQ_BANNER))

    session_factory = get_session_factory(DATABASE_URL)
    async with session_factory() as session:
        stmt = select(APIKey).order_by(APIKey.id.desc()).limit(20)
        keys = (await session.execute(stmt)).scalars().all()

        if not keys:
            console.print("[yellow]В базе данных пока нет ключей.[/]")
        else:
            table = Table(title="Последние 20 ключей", box=box.ROUNDED)
            table.add_column("ID", style="dim")
            table.add_column("Тип", style=COLORS["info"])
            table.add_column("Ключ", style=COLORS["success"])
            table.add_column("Статус")
            table.add_column("Дата нахождения")

            for k in keys:
                status_color = "green" if k.status == ApiStatusEnum.VALID else "red" if k.status == ApiStatusEnum.INVALID else "white"
                table.add_row(
                    str(k.id),
                    k.api_type.name if hasattr(k.api_type, 'name') else str(k.api_type),
                    f"{k.api_key[:30]}...",
                    f"[{status_color}]{k.status.name}[/]",
                    k.first_found_utc.strftime("%Y-%m-%d %H:%M")
                )
            console.print(table)

    Prompt.ask("\nНажмите Enter")

async def manage_queries():
    while True:
        console.clear()
        console.print(Align.center(SQ_BANNER))

        session_factory = get_session_factory(DATABASE_URL)
        async with session_factory() as session:
            queries = (await session.execute(select(SearchQuery))).scalars().all()

            table = Table(title="Поисковые Запросы", box=box.ROUNDED)
            table.add_column("ID", style="dim")
            table.add_column("Запрос", style=COLORS["info"])
            table.add_column("Активен", justify="center")

            for q in queries:
                active = "[green]YES[/]" if q.is_enabled else "[red]NO[/]"
                table.add_row(str(q.id), q.query, active)
            console.print(table)

        console.print("\n[bold]1.[/] Добавить запрос")
        console.print("[bold]2.[/] Удалить запрос")
        console.print("[bold]0.[/] Назад")

        choice = Prompt.ask("Выберите действие", choices=["0", "1", "2"], default="0")

        if choice == "1":
            new_q = Prompt.ask("Введите запрос (напр. 'sk-proj-')")
            if new_q:
                async with session_factory() as session:
                    session.add(SearchQuery(query=new_q, is_enabled=True, last_search_utc=datetime.now(timezone.utc) - timedelta(days=1)))
                    await session.commit()
        elif choice == "2":
            qid = Prompt.ask("Введите ID для удаления")
            if qid.isdigit():
                async with session_factory() as session:
                    await session.execute(delete(SearchQuery).where(SearchQuery.id == int(qid)))
                    await session.commit()
        else:
            break

async def clear_database():
    if Confirm.ask("[bold red]ВЫ УВЕРЕНЫ, ЧТО ХОТИТЕ ОЧИСТИТЬ ВСЮ БАЗУ ДАННЫХ?[/]", default=False):
        session_factory = get_session_factory(DATABASE_URL)
        async with session_factory() as session:
            await session.execute(delete(APIKey))
            await session.commit()
        console.print("[green]✅ База данных очищена.[/]")
        await asyncio.sleep(1)

async def quick_check():
    console.clear()
    console.print(Align.center(SQ_BANNER))
    key = Prompt.ask("[bold #C77DFF]Введите API ключ для проверки[/]")
    if not key: return

    from python_version.src.providers.registry import ApiProviderRegistry
    registry = ApiProviderRegistry()
    providers = registry.get_all_providers()

    target_provider = None
    for p in providers:
        for pat in p.regex_patterns:
            if re.search(pat, key):
                target_provider = p
                break
        if target_provider: break

    if not target_provider:
        console.print("[red]❌ Формат ключа не распознан.[/]")
        Prompt.ask("Нажмите Enter")
        return

    console.print(f"[*] Провайдер: [bold #00F5D4]{target_provider.provider_name}[/]")
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            result = await target_provider.validate_key_async(key, client)
            status_color = "#00F5D4" if result.status.name == "VALID" else "#F72585"
            console.print(f"\n[bold]РЕЗУЛЬТАТ:[/] [bold {status_color}]{result.status.name}[/]")
            if result.detail:
                console.print(f"[dim]Инфо: {result.detail}[/]")
        except Exception as e:
            console.print(f"[red]❌ Ошибка проверки: {e}[/]")
    Prompt.ask("\nНажмите Enter")

async def export_db():
    console.clear()
    console.print("[*] Экспорт...")
    session_factory = get_session_factory(DATABASE_URL)
    async with session_factory() as session:
        keys = (await session.execute(select(APIKey))).scalars().all()
        with open(FOUND_KEYS_TXT, "w", encoding="utf-8") as f:
            for k in keys: f.write(f"{k.first_found_utc} | {k.api_key} | {k.status.name}\n")
        data = [{"key": k.api_key, "status": k.status.name, "type": k.api_type.name} for k in keys]
        with open(FOUND_KEYS_JSON, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
    console.print(f"[green]✅ Успешно: {len(keys)} ключей.[/]")
    await asyncio.sleep(1)

async def main_menu():
    load_dotenv()
    await init_db(DATABASE_URL)
    session_factory = get_session_factory(DATABASE_URL)

    async with session_factory() as session:
        if (await session.scalar(select(func.count()).select_from(SearchQuery))) == 0:
            for q in ["sk-", "sk-proj-", "AIza", "gsk_", "pplx-", "xai-", "ghp_", "glpat-"]:
                session.add(SearchQuery(query=q, is_enabled=True, last_search_utc=datetime.now(timezone.utc) - timedelta(days=1)))
            await session.commit()

    while True:
        console.clear()
        console.print(Align.center(SQ_BANNER))

        menu = Table(box=None, show_header=False)
        menu.add_column("ID", style="bold #9D4EDD")
        menu.add_column("Text", style="white")

        items = [
            ("1.", "🚀 Начать парсинг (Start Scan)"),
            ("2.", "🔍 Просмотр ключей (View Keys)"),
            ("3.", "⚙️ Управление запросами (Queries)"),
            ("4.", "✅ Проверить БД (Validate DB)"),
            ("5.", "🔍 Быстрая проверка (Quick Check)"),
            ("6.", "📦 Экспорт (Export Data)"),
            ("7.", "🗑️ Очистить БД (Clear DB)"),
            ("0.", "❌ Выход (Exit)")
        ]
        for idx, text in items: menu.add_row(idx, text)
        console.print(Align.center(Panel(menu, title="[bold white]MAIN MENU[/]", border_style="#00F5D4", padding=(1, 2))))

        async with session_factory() as session:
            count = (await session.execute(select(func.count(APIKey.id)))).scalar()
            console.print(Align.center(Text(f"База данных: {count} ключей | {DATABASE_URL.split('/')[-1]}", style="dim")))

        choice = Prompt.ask("Выберите пункт", choices=[i[0].replace(".", "") for i in items], default="1")

        if choice == "1": await start_scanning()
        elif choice == "2": await view_keys()
        elif choice == "3": await manage_queries()
        elif choice == "4":
            from python_version.src.sq_scanner import verify_keys_db
            await verify_keys_db()
        elif choice == "5": await quick_check()
        elif choice == "6": await export_db()
        elif choice == "7": await clear_database()
        elif choice == "0": break

if __name__ == "__main__":
    logging.basicConfig(level=logging.ERROR, filename="sq_scanner.log")
    try: asyncio.run(main_menu())
    except KeyboardInterrupt: pass

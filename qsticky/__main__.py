import asyncio

from .manager import PortManager


async def main() -> None:
    manager = PortManager()
    try:
        manager.setup_signal_handlers()
        tasks = [
            asyncio.create_task(manager.watch_port())
        ]
        await manager.shutdown_event.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await manager.cleanup()


if __name__ == "__main__":
    asyncio.run(main())

from dataclasses import dataclass
from datetime import UTC, datetime


def now_utc() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ClockContext:
    tick_index: int
    tick_duration: str
    elapsed_since_big_bang: str
    elapsed_since_previous_tick: str

    def as_prompt_text(self) -> str:
        return (
            f"Current tick: T{self.tick_index}\n"
            f"Tick duration: {self.tick_duration}\n"
            f"Elapsed since Big Bang: {self.elapsed_since_big_bang}\n"
            f"Elapsed since previous tick: {self.elapsed_since_previous_tick}"
        )


def build_clock_context(tick_index: int, tick_duration: str) -> ClockContext:
    elapsed = f"{tick_index} * {tick_duration}"
    previous = tick_duration if tick_index > 0 else "0"
    return ClockContext(
        tick_index=tick_index,
        tick_duration=tick_duration,
        elapsed_since_big_bang=elapsed,
        elapsed_since_previous_tick=previous,
    )

"""Observability sink — ships prompt logs, usage and audit events to Kafka.

Per the task scope we only *produce* to Kafka (consumption / analytics is out
of scope). Two implementations:

* ``KafkaSink`` — aiokafka producer, fire-and-forget, JSON-encoded.
* ``NullSink`` — drops everything; used when Kafka is disabled or for tests.

A failure to publish must never break a proxied request, so ``KafkaSink``
swallows producer errors after logging them.
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("ghcproxy.sink")


class NullSink:
    async def start(self) -> None:  # pragma: no cover - trivial
        pass

    async def stop(self) -> None:  # pragma: no cover - trivial
        pass

    async def prompt(self, event: dict[str, Any]) -> None:
        pass

    async def usage(self, event: dict[str, Any]) -> None:
        pass

    async def audit(self, event: dict[str, Any]) -> None:
        pass


class KafkaSink:
    def __init__(self, brokers: list[str], topic_prompts: str,
                 topic_usage: str, topic_audit: str) -> None:
        self._brokers = brokers
        self._topics = {"prompt": topic_prompts, "usage": topic_usage, "audit": topic_audit}
        self._producer = None

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer  # imported lazily

        self._producer = AIOKafkaProducer(
            bootstrap_servers=",".join(self._brokers),
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            linger_ms=20,
        )
        await self._producer.start()

    async def stop(self) -> None:
        if self._producer:
            await self._producer.stop()

    async def _send(self, kind: str, event: dict[str, Any]) -> None:
        if not self._producer:
            return
        try:
            await self._producer.send_and_wait(self._topics[kind], event)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("kafka publish failed (%s): %s", kind, exc)

    async def prompt(self, event: dict[str, Any]) -> None:
        await self._send("prompt", event)

    async def usage(self, event: dict[str, Any]) -> None:
        await self._send("usage", event)

    async def audit(self, event: dict[str, Any]) -> None:
        await self._send("audit", event)

"""Как сервер стадии превращает сообщения в токены."""

from __future__ import annotations

import logging

from looma_stage import server


class _Tokenizer:
    def __init__(self, template):
        self.chat_template = template

    def apply_chat_template(self, messages, **_kwargs):
        return [1, 2, 3]

    def encode(self, text):
        return [ord(ch) for ch in text]


def test_с_шаблоном_токены_из_шаблона():
    assert server._encode_chat(_Tokenizer("{{ messages }}"),
                               [{"role": "user", "content": "привет"}]) == [1, 2, 3]


def test_без_шаблона_плоский_текст_и_предупреждение_один_раз(monkeypatch, caplog):
    """Плоский текст — законный путь для базовой модели, но у instruct-модели
    он ломает ответ целиком, и по ответу этого не понять. Со стенда: gpt-oss
    выдумывал диалоги, пока chat_template.jinja просто не доехал на узел."""
    monkeypatch.setattr(server, "_no_template_said", False)
    tokenizer = _Tokenizer(None)
    with caplog.at_level(logging.WARNING, logger=server.logger.name):
        ids = server._encode_chat(tokenizer, [{"role": "user", "content": "hi"}])
        server._encode_chat(tokenizer, [{"role": "user", "content": "hi"}])
    assert ids == [ord(ch) for ch in "user: hi\nassistant:"]
    assert caplog.text.count("нет шаблона чата") == 1, "один раз на процесс"
    assert "chat_template.jinja" in caplog.text

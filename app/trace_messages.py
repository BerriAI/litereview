"""Conversation capture for the three model protocols relayed by LiteReview.

Keep messages and tool interactions, not request envelopes, schemas, images, or
encrypted reasoning. These helpers are only used when content capture is enabled.
"""

import json


def encoded(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def text_content(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text for item in value if (text := text_content(item)))
    if isinstance(value, dict):
        if value.get("type") in ("image", "image_url", "input_image", "output_image", "compaction"):
            return ""
        for key in ("text", "content", "refusal", "summary"):
            if key in value:
                return text_content(value[key])
    return ""


def tool_call(item):
    function = item.get("function") or item
    return {
        "id": item.get("call_id") or item.get("id", ""),
        "type": "function",
        "function": {
            "name": function.get("name", ""),
            "arguments": encoded(function.get("arguments", function.get("input", ""))),
        },
    }


def messages(value, default_role="user"):
    if isinstance(value, str):
        return [{"role": default_role, "content": value}] if value else []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        kind = item.get("type", "")
        if kind in ("function_call", "custom_tool_call", "tool_use"):
            result.append({"role": "assistant", "content": "", "tool_calls": [tool_call(item)]})
        elif kind in ("function_call_output", "custom_tool_call_output", "tool_result"):
            output = item.get("output", item.get("content", ""))
            result.append({
                "role": "tool", "content": text_content(output),
                "tool_call_id": item.get("call_id", item.get("tool_use_id", "")),
            })
        elif kind == "reasoning":
            if summary := text_content(item.get("summary")):
                result.append({"role": "assistant", "name": "reasoning", "content": summary})
        elif "role" in item or "content" in item or kind in ("text", "output_text"):
            content = item.get("content", item.get("text", ""))
            parts = content if isinstance(content, list) else []
            calls = [tool_call(call) for call in item.get("tool_calls") or [] if isinstance(call, dict)]
            calls += [tool_call(part) for part in parts
                      if isinstance(part, dict) and part.get("type") == "tool_use"]
            text = text_content([p for p in parts if isinstance(p, dict) and p.get("type") != "tool_result"]
                                if parts else content)
            message = {"role": item.get("role", default_role), "content": text}
            for key in ("name", "tool_call_id"):
                if item.get(key):
                    message[key] = item[key]
            if calls:
                message["tool_calls"] = calls
            if text or calls:
                result.append(message)
            result += messages([part for part in parts
                                if isinstance(part, dict) and part.get("type") == "tool_result"])
    names = {call["id"]: call["function"]["name"]
             for message in result for call in message.get("tool_calls", [])}
    for message in result:
        if message.get("tool_call_id") in names:
            message["name"] = names[message["tool_call_id"]]
    return result


def request_messages(body):
    return (messages(body.get("system", ""), "system")
            + messages(body.get("instructions", ""), "system")
            + messages(body.get("messages", body.get("input", ""))))


def message_attributes(direction, conversation, response_id=None):
    raw = {"messages": conversation}
    if response_id:
        raw["id"] = response_id
    attrs = {direction + ".value": encoded(raw), direction + ".mime_type": "application/json"}
    for index, message in enumerate(conversation):
        prefix = f"llm.{direction}_messages.{index}.message."
        for key in ("role", "content", "name", "tool_call_id"):
            if key in message:
                attrs[prefix + key] = message[key]
        for index, call in enumerate(message.get("tool_calls", [])):
            base = prefix + f"tool_calls.{index}.tool_call."
            attrs[base + "id"] = call["id"]
            attrs[base + "function.name"] = call["function"]["name"]
            attrs[base + "function.arguments"] = call["function"]["arguments"]
    return attrs


class ResponseMessages:
    """Assemble deltas; complete items replace their deltas instead of repeating them."""

    def __init__(self):
        self.response_id = None
        self.final = None
        self.items = {}
        self.choices = {}
        self.blocks = {}
        self.fallback = ""

    def add(self, data):
        event = data.get("type", "")
        response = data.get("response") or data.get("message") or data
        if isinstance(response, dict):
            self.response_id = response.get("id") or self.response_id
            if response.get("output"):
                self.final = messages(response["output"], "assistant")
            elif response.get("content") or response.get("tool_calls"):
                self.final = messages(response, "assistant")
        if event in ("response.output_item.added", "response.output_item.done"):
            self.items[data["output_index"]] = data["item"]
        elif event in ("response.function_call_arguments.delta", "response.function_call_arguments.done",
                       "response.custom_tool_call_input.delta", "response.custom_tool_call_input.done"):
            field = "input" if "custom_tool" in event else "arguments"
            item = self.items.setdefault(data["output_index"], {"type": "function_call"})
            item[field] = data.get(field, "") if event.endswith(".done") else item.get(field, "") + data["delta"]
        elif event in ("response.output_text.delta", "response.output_text.done"):
            item = self.items.setdefault(data.get("output_index", 0), {"role": "assistant", "content": []})
            content = item.setdefault("content", [])
            index = data.get("content_index", 0)
            while len(content) <= index:
                content.append({"type": "output_text", "text": ""})
            content[index]["text"] = (data.get("text", "") if event.endswith(".done")
                                      else content[index].get("text", "") + data["delta"])
        elif event == "content_block_start":
            self.blocks[data["index"]] = data["content_block"]
        elif event == "content_block_delta":
            delta = data["delta"]
            block = self.blocks.setdefault(data.get("index", 0), {"type": "text", "text": ""})
            if delta.get("type") == "input_json_delta":
                block["_arguments"] = block.get("_arguments", "") + delta["partial_json"]
            elif "text" in delta:
                block["text"] = block.get("text", "") + delta["text"]
        elif isinstance(data.get("delta"), dict) and data["delta"].get("text"):
            self.fallback += data["delta"]["text"]
        for choice in data.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            index = choice.get("index", 0)
            if choice.get("message"):
                self.choices[index] = choice["message"]
                continue
            delta = choice.get("delta") or {}
            message = self.choices.setdefault(index, {"role": "assistant", "content": "", "tool_calls": []})
            if delta.get("content"):
                message["content"] = (message.get("content") or "") + delta["content"]
            for call in delta.get("tool_calls") or []:
                calls = message.setdefault("tool_calls", [])
                position = call.get("index", 0)
                while len(calls) <= position:
                    calls.append({"id": "", "function": {"name": "", "arguments": ""}})
                target = calls[position]
                if call.get("id"):
                    target["id"] = call["id"]
                for field in ("name", "arguments"):
                    target["function"][field] += (call.get("function") or {}).get(field) or ""

    def conversation(self):
        if self.final is not None:
            return self.final
        if self.choices:
            return messages([v for _, v in sorted(self.choices.items())], "assistant")
        if self.items:
            return messages([v for _, v in sorted(self.items.items())], "assistant")
        if self.blocks:
            blocks = []
            for _, block in sorted(self.blocks.items()):
                block = dict(block)
                if "_arguments" in block:
                    block["input"] = block.pop("_arguments")
                blocks.append(block)
            return messages({"role": "assistant", "content": blocks})
        return messages(self.fallback, "assistant")

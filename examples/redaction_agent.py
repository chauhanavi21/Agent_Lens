"""
Tracing a support agent without storing customer data.

Support tickets are the worst case for trace storage: the prompt is
whatever the customer typed, which routinely includes an email address, an
order number, sometimes a card number they pasted in frustration. None of
that was deliberately logged, and all of it ends up in a trace backend.

    python examples/redaction_agent.py
"""

from agentlens import AgentLens, ConsoleExporter, Redactor, SpanKind

# Policies chosen per field rather than one blanket setting:
#   hash  → still correlate "same customer, three tickets" without the value
#   mask  → keep a recognizable shape for debugging
#   drop  → nothing survives
redactor = Redactor(
    policies={
        "email": "hash",
        "phone": "mask",
        "credit_card": "drop",
        "ssn": "drop",
        "ipv4": "allow",          # internal service IPs are useful, not sensitive
    },
    extra_patterns={"order_id": r"\bORD-\d{8}\b"},
)

lens = AgentLens(exporter=ConsoleExporter(), redact=redactor)

runs = []
lens.exporter = type("Capture", (), {"export": lambda self, run: runs.append(run.to_dict())})()


@lens.tool("lookup_order")
def lookup_order(order_id, email):
    return {
        "order_id": order_id,
        "customer_email": email,
        "phone": "(555) 123-4567",
        "payment": {"card_number": "4111 1111 1111 1111", "status": "captured"},
        "warehouse_ip": "10.2.0.14",
    }


@lens.span("draft_reply", kind=SpanKind.LLM)
def draft_reply(order):
    return f"Hi, order {order['order_id']} shipped. We'll email {order['customer_email']}."


@lens.trace("support_agent", tags=["prod", "pii"])
def support_agent(message, email):
    order = lookup_order("ORD-48210033", email)
    return draft_reply(order)


if __name__ == "__main__":
    ticket = "My card 4111 1111 1111 1111 was charged twice for ORD-48210033!"
    print("customer wrote:", ticket)
    print("agent replied: ", support_agent(ticket, "jane.doe@acme.com"))

    print("\nwhat actually got stored:")
    for span in runs[0]["spans"]:
        print(f"  [{span['kind']}] {span['name']}")
        if span["inputs"]:
            print(f"      in:  {span['inputs'][:150]}")
        if span["outputs"]:
            print(f"      out: {span['outputs'][:150]}")

    stored = str(runs[0])
    for secret in ("4111 1111 1111 1111", "jane.doe@acme.com", "ORD-48210033"):
        assert secret not in stored, f"{secret} leaked!"
    print("\nno card number, email, or order id reached the exporter.")
    print("The same customer still hashes to the same token across runs,")
    print("so you can group their tickets without storing who they are.")

    # dry run: see what a redactor would catch before turning it on
    print("\nscan of the raw ticket:", redactor.scan(ticket))

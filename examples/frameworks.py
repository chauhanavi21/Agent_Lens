"""
Wiring AgentLens into the agent frameworks.

Every integration is one line and needs no change to the agent itself.
Runs from all of them land in the same UI with the same DAG, diffing, and
eval scoring — which is the point of tracing the agent rather than the
LLM client.

    python examples/frameworks.py
"""

from agentlens import AgentLens

ENDPOINT = "http://localhost:7430"


def openai_agents_sdk():
    """Register a processor; the SDK's own tracing does the rest."""
    from agents import Agent, Runner, add_trace_processor           # noqa: F401
    from agentlens.integrations.openai_agents import AgentLensTracingProcessor

    lens = AgentLens(endpoint=ENDPOINT)
    add_trace_processor(AgentLensTracingProcessor(lens))
    # add_trace_processor keeps OpenAI's dashboard too;
    # set_trace_processors([...]) would replace it.

    agent = Agent(name="support", instructions="Help the customer.")
    return Runner.run_sync(agent, "where is my order?")


def langgraph():
    """Wrap the compiled graph; each node becomes a span."""
    from agentlens.integrations.langgraph import trace_graph

    lens = AgentLens(endpoint=ENDPOINT)
    # app = trace_graph(lens, graph.compile(), run_name="support_graph")
    # app.invoke({"messages": [...]})
    #
    # Each node records which state keys it changed, so a loop shows up as
    # the same node repeating with a shrinking diff.
    return trace_graph


def pydantic_ai():
    """Wrap the agent; tool functions get wrapped with it."""
    from agentlens.integrations.pydantic_ai import trace_agent

    lens = AgentLens(endpoint=ENDPOINT)
    # agent = trace_agent(lens, Agent("openai:gpt-4o", tools=[get_weather]))
    # await agent.run("weather in Lisbon?")
    #
    # Already using Logfire? agent.instrument_all() emits OTel spans; point
    # your collector at /api/ingest/otlp instead of using this wrapper.
    return trace_agent


def langchain():
    """Pass the handler in the config, then close the run."""
    from agentlens.integrations.langchain import AgentLensCallbackHandler

    lens = AgentLens(endpoint=ENDPOINT)
    handler = AgentLensCallbackHandler(lens, run_name="my_chain")
    # chain.invoke(inputs, config={"callbacks": [handler]})
    # handler.end()
    return handler


def crewai():
    """Wrap the crew; each task becomes a span."""
    from agentlens.integrations.crewai import trace_crew

    lens = AgentLens(endpoint=ENDPOINT)
    # trace_crew(lens, crew, run_name="research_crew").kickoff(inputs={...})
    return trace_crew


if __name__ == "__main__":
    print("AgentLens integrations, one line each:\n")
    print("  OpenAI Agents SDK  add_trace_processor(AgentLensTracingProcessor(lens))")
    print("  LangGraph          app = trace_graph(lens, graph.compile())")
    print("  Pydantic AI        agent = trace_agent(lens, agent)")
    print("  LangChain          config={'callbacks': [AgentLensCallbackHandler(lens)]}")
    print("  CrewAI             trace_crew(lens, crew).kickoff(...)")
    print("\nNo framework installed? Every module above imports fine anyway —")
    print("the adapters read framework objects by duck typing, so importing")
    print("agentlens never drags in LangChain or anything else.")

    for module in ("openai_agents", "langgraph", "pydantic_ai", "langchain", "crewai"):
        __import__(f"agentlens.integrations.{module}")
    print("\nAll five integration modules imported with zero frameworks present.")

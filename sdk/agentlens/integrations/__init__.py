"""
Framework integrations.

Each module imports its framework lazily or not at all, so importing
agentlens never requires LangChain, LangGraph, or any other package to be
installed. Adapters read framework objects by duck typing rather than by
importing their types: these APIs are still moving, and a renamed attribute
should cost one field, not the whole trace.

    langchain      AgentLensCallbackHandler
    langgraph      trace_graph
    crewai         trace_crew
    openai_agents  AgentLensTracingProcessor
    pydantic_ai    trace_agent
"""

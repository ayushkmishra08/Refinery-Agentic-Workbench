"""DAG executor: runs Plan.ready_steps() (sequential first; parallel later), collects AgentResults, calls SafetyAgent on safety_sensitive steps, raises ReplanRequested on needs_replan."""

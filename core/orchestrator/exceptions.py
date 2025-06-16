class OrchestrationError(Exception):
    def __init__(self, message: str, flow_id: str = "", stage: str = ""):
        super().__init__(message)
        self.flow_id = flow_id
        self.stage = stage

    def __str__(self):
        context = f" [Flow: {self.flow_id}, Stage: {self.stage}]" if self.flow_id or self.stage else ""
        return f"OrchestrationError: {self.args[0]}{context}"

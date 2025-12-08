from temporalio import workflow

class StepOneHelper:
    async def do_section1(self, arg):
        activity1(arg)
        activity2(arg)
        return result

class StepTwoHelper:
    async def do_section2(self, arg):
        activity3(arg)
        activity4(arg)
        return result

@workflow.defn
class LargeWorkflow:
    def __init__(self):
        self.step_one = StepOneHelper()
        self.step_two = StepTwoHelper()

    @workflow.run
    async def run(self, input):
        result1 = await self.step_one.do_section1(input)
        result2 = await self.step_two.do_section2(result1)
        return result2

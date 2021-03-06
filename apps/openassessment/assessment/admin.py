from django.contrib import admin

from openassessment.assessment.models import (
    Assessment, AssessmentPart, Rubric,
    AssessmentFeedback, AssessmentFeedbackOption,
    Criterion, CriterionOption,
    PeerWorkflow, PeerWorkflowItem,
)

admin.site.register(Assessment)
admin.site.register(AssessmentPart)
admin.site.register(AssessmentFeedback)
admin.site.register(AssessmentFeedbackOption)
admin.site.register(Rubric)
admin.site.register(Criterion)
admin.site.register(CriterionOption)
admin.site.register(PeerWorkflow)
admin.site.register(PeerWorkflowItem)

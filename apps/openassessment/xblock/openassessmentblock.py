"""An XBlock where students can read a question and compose their response"""

import datetime as dt
import logging
import dateutil
import pkg_resources

import pytz

from django.template.context import Context
from django.template.loader import get_template
from webob import Response

from xblock.core import XBlock
from xblock.fields import List, Scope, String, Boolean
from xblock.fragment import Fragment
from openassessment.xblock.grade_mixin import GradeMixin

from openassessment.xblock.peer_assessment_mixin import PeerAssessmentMixin
from openassessment.xblock.lms_mixin import LmsCompatibilityMixin
from openassessment.xblock.self_assessment_mixin import SelfAssessmentMixin
from openassessment.xblock.submission_mixin import SubmissionMixin
from openassessment.xblock.studio_mixin import StudioMixin
from openassessment.xblock.xml import update_from_xml, serialize_content_to_xml
from openassessment.xblock.workflow_mixin import WorkflowMixin
from openassessment.workflow import api as workflow_api
from openassessment.xblock.validation import validator
from openassessment.xblock.resolve_dates import resolve_dates


logger = logging.getLogger(__name__)


DEFAULT_PROMPT = """
    Censorship in the Libraries

    'All of us can think of a book that we hope none of our children or any
    other children have taken off the shelf. But if I have the right to remove
    that book from the shelf -- that work I abhor -- then you also have exactly
    the same right and so does everyone else. And then we have no books left on
    the shelf for any of us.' --Katherine Paterson, Author

    Write a persuasive essay to a newspaper reflecting your views on censorship
    in libraries. Do you believe that certain materials, such as books, music,
    movies, magazines, etc., should be removed from the shelves if they are
    found offensive? Support your position with convincing arguments from your
    own experience, observations, and/or reading.

    Read for conciseness, clarity of thought, and form.
"""

DEFAULT_RUBRIC_CRITERIA = [
    {
        'name': "Ideas",
        'prompt': "Determine if there is a unifying theme or main idea.",
        'order_num': 0,
        'options': [
            {
                'order_num': 0, 'points': 0, 'name': 'Poor',
                'explanation': """Difficult for the reader to discern the main idea.
                Too brief or too repetitive to establish or maintain a focus."""
            },
            {
                'order_num': 1, 'points': 3, 'name': 'Fair',
                'explanation': """Presents a unifying theme or main idea, but may
                include minor tangents.  Stays somewhat focused on topic and
                task."""
            },
            {
                'order_num': 2, 'points': 5, 'name': 'Good',
                'explanation': """Presents a unifying theme or main idea without going
                off on tangents.  Stays completely focused on topic and task."""
            },
        ],
    },
    {
        'name': "Content",
        'prompt': "Assess the content of the submission",
        'order_num': 1,
        'options': [
            {
                'order_num': 0, 'points': 0, 'name': 'Poor',
                'explanation': """Includes little information with few or no details or
                unrelated details.  Unsuccessful in attempts to explore any
                facets of the topic."""
            },
            {
                'order_num': 1, 'points': 1, 'name': 'Fair',
                'explanation': """Includes little information and few or no details.
                Explores only one or two facets of the topic."""
            },
            {
                'order_num': 2, 'points': 3, 'name': 'Good',
                'explanation': """Includes sufficient information and supporting
                details. (Details may not be fully developed; ideas may be
                listed.)  Explores some facets of the topic."""
            },
            {
                'order_num': 3, 'points': 3, 'name': 'Excellent',
                'explanation': """Includes in-depth information and exceptional
                supporting details that are fully developed.  Explores all
                facets of the topic."""
            },
        ],
    },
]

UI_MODELS = {
    "submission": {
        "name": "submission",
        "class_id": "openassessment__response",
        "navigation_text": "Your response to this problem",
        "title": "Your Response"
    },
    "peer-assessment": {
        "name": "peer-assessment",
        "class_id": "openassessment__peer-assessment",
        "navigation_text": "Your assessment(s) of peer responses",
        "title": "Assess Peers' Responses"
    },
    "self-assessment": {
        "name": "self-assessment",
        "class_id": "openassessment__self-assessment",
        "navigation_text": "Your assessment of your response",
        "title": "Assess Your Response"
    },
    "grade": {
        "name": "grade",
        "class_id": "openassessment__grade",
        "navigation_text": "Your grade for this problem",
        "title": "Your Grade:"
    }
}

"""
The Default Peer Assessment is created as an example of how this XBlock can be
configured. If no configuration is specified, this is the default assessment
module(s) associated with the XBlock.
"""
DEFAULT_PEER_ASSESSMENT = {
    "name": "peer-assessment",
    "start": None,
    "due": None,
    "must_grade": 5,
    "must_be_graded_by": 3,
}

DEFAULT_SELF_ASSESSMENT = {
    "name": "self-assessment",
    "due": None,
}


DEFAULT_ASSESSMENT_MODULES = [
    DEFAULT_PEER_ASSESSMENT,
    DEFAULT_SELF_ASSESSMENT,
]

DATE_FORMAT = "%A, %B %d, %Y"
DATETIME_FORMAT = "%A, %B %d, %Y %X"


def load(path):
    """Handy helper for getting resources from our kit."""
    data = pkg_resources.resource_string(__name__, path)
    return data.decode("utf8")


class OpenAssessmentBlock(
    XBlock,
    SubmissionMixin,
    PeerAssessmentMixin,
    SelfAssessmentMixin,
    StudioMixin,
    GradeMixin,
    WorkflowMixin,
    LmsCompatibilityMixin):
    """Displays a question and gives an area where students can compose a response."""

    submission_due = String(
        default=None, scope=Scope.settings,
        help="ISO-8601 formatted string representing the submission due date."
    )

    title = String(
        default="",
        scope=Scope.content,
        help="A title to display to a student (plain text)."
    )

    prompt = String(
        default=DEFAULT_PROMPT,
        scope=Scope.content,
        help="A prompt to display to a student (plain text)."
    )

    rubric_criteria = List(
        default=DEFAULT_RUBRIC_CRITERIA,
        scope=Scope.content,
        help="The different parts of grading for students giving feedback."
    )

    rubric_assessments = List(
        default=DEFAULT_ASSESSMENT_MODULES,
        scope=Scope.content,
        help="The requested set of assessments and the order in which to apply them."
    )

    course_id = String(
        default=u"TestCourse",
        scope=Scope.content,
        help="The course_id associated with this prompt (until we can get it from runtime).",
    )
    submission_uuid = String(
        default=None,
        scope=Scope.user_state,
        help="The student's submission that others will be assessing."
    )

    has_saved = Boolean(
        default=False,
        scope=Scope.user_state,
        help="Indicates whether the user has saved a response"
    )

    saved_response = String(
        default=u"",
        scope=Scope.user_state,
        help="Saved response submission for the current user."
    )

    def get_student_item_dict(self):
        """Create a student_item_dict from our surrounding context.

        See also: submissions.api for details.

        Returns:
            (dict): The student item associated with this XBlock instance. This
                includes the student id, item id, and course id.
        """
        item_id = unicode(self.scope_ids.usage_id)

        # This is not the real way course_ids should work, but this is a
        # temporary expediency for LMS integratino
        if hasattr(self, "xmodule_runtime"):
            course_id = self.xmodule_runtime.course_id
            student_id = self.xmodule_runtime.anonymous_student_id
        else:
            course_id = "edX/Enchantment_101/April_1"
            if self.scope_ids.user_id is None:
                student_id = None
            else:
                student_id = unicode(self.scope_ids.user_id)

        student_item_dict = dict(
            student_id=student_id,
            item_id=item_id,
            course_id=course_id,
            item_type='openassessment'
        )
        return student_item_dict

    def student_view(self, context=None):
        """The main view of OpenAssessmentBlock, displayed when viewing courses.

        The main view which displays the general layout for Open Ended
        Assessment Questions. The contents of the XBlock are determined
        dynamically based on the assessment workflow configured by the author.

        Args:
            context: Not used for this view.

        Returns:
            (Fragment): The HTML Fragment for this XBlock, which determines the
            general frame of the Open Ended Assessment Question.
        """
        # On page load, update the workflow status.
        # We need to do this here because peers may have graded us, in which
        # case we may have a score available.
        try:
            self.update_workflow_status()
        except workflow_api.AssessmentWorkflowError:
            # Log the exception, but continue loading the page
            logger.exception('An error occurred while updating the workflow on page load.')

        ui_models = self._create_ui_models()
        # All data we intend to pass to the front end.
        context_dict = {
            "title": self.title,
            "question": self.prompt,
            "rubric_criteria": self.rubric_criteria,
            "rubric_assessments": ui_models,
            "is_course_staff": False,
        }

        if self.is_course_staff and not self.in_studio_preview:
            status_counts, num_submissions = self.get_workflow_status_counts()
            context_dict['is_course_staff'] = True
            context_dict['status_counts'] = status_counts
            context_dict['num_submissions'] = num_submissions
            context_dict['item_id'] = unicode(self.scope_ids.usage_id)

        template = get_template("openassessmentblock/oa_base.html")
        context = Context(context_dict)
        frag = Fragment(template.render(context))
        frag.add_css(load("static/css/openassessment.css"))
        frag.add_javascript(load("static/js/openassessment.min.js"))
        frag.initialize_js('OpenAssessmentBlock')
        return frag

    @property
    def is_course_staff(self):
        """
        Check whether the user has course staff permissions for this XBlock.

        Returns:
            bool
        """
        if hasattr(self, 'xmodule_runtime'):
            return getattr(self.xmodule_runtime, 'user_is_staff', False)
        else:
            return False

    @property
    def in_studio_preview(self):
        """
        Check whether we are in Studio preview mode.

        Returns:
            bool

        """
        # When we're running in Studio Preview mode, the XBlock won't provide us with a user ID.
        # (Note that `self.xmodule_runtime` will still provide an anonymous
        # student ID, so we can't rely on that)
        return self.scope_ids.user_id is None

    def _create_ui_models(self):
        """Combine UI attributes and XBlock configuration into a UI model.

        This method takes all configuration for this XBlock instance and appends
        UI attributes to create a UI Model for rendering all assessment modules.
        This allows a clean separation of static UI attributes from persistent
        XBlock configuration.

        """
        ui_models = [UI_MODELS["submission"]]
        for assessment in self.rubric_assessments:
            ui_model = UI_MODELS[assessment["name"]]
            ui_models.append(dict(assessment, **ui_model))
        ui_models.append(UI_MODELS["grade"])
        return ui_models

    @staticmethod
    def workbench_scenarios():
        """A canned scenario for display in the workbench.

        These scenarios are only intended to be used for Workbench XBlock
        Development.

        """
        return [
            (
                "OpenAssessmentBlock Poverty Rubric",
                load('static/xml/poverty_rubric_example.xml')
            ),
            (
                "OpenAssessmentBlock Censorship Rubric",
                load('static/xml/censorship_rubric_example.xml')
            ),
        ]

    @classmethod
    def parse_xml(cls, node, runtime, keys, id_generator):
        """Instantiate XBlock object from runtime XML definition.

        Inherited by XBlock core.

        """
        def unknown_handler(block, child):
            """Recursively embed xblocks for nodes we don't recognize"""
            block.runtime.add_node_as_child(block, child, id_generator)
        block = runtime.construct_xblock_from_class(cls, keys)

        return update_from_xml(block, node, validator=validator(block, strict_post_release=False))

    def render_assessment(self, path, context_dict=None):
        """Render an Assessment Module's HTML

        Given the name of an assessment module, find it in the list of
        configured modules, and ask for its rendered HTML.

        Args:
            path (str): The path to the template used to render this HTML
                section.
            context_dict (dict): A dictionary of context variables used to
                populate this HTML section.

        Returns:
            (Response): A Response Object with the generated HTML fragment. This
                is intended for AJAX calls to load dynamically into a larger
                document.
        """
        if not context_dict:
            context_dict = {}

        if self.start:
            context_dict["formatted_start_date"] = self.start.strftime(DATE_FORMAT)
            context_dict["formatted_start_datetime"] = self.start.strftime(DATETIME_FORMAT)
        if self.due:
            context_dict["formatted_due_date"] = self.due.strftime(DATE_FORMAT)
            context_dict["formatted_due_datetime"] = self.due.strftime(DATETIME_FORMAT)

        template = get_template(path)
        context = Context(context_dict)
        return Response(template.render(context), content_type='application/html', charset='UTF-8')

    def format_datetime_string(self, datetime_str):
        """Takes a datetime string, and formats it to be a user facing time.

        Format a datetime string to be user facing. Datetimes are stored as
        strings in the XBlock configuration for an assessment module. This
        function is used to get back the Datetime object for rendering the start
        and due dates on the front end.

        Args:
            datetime_str (str): A ISO formatted Datetime String, to be converted
                to a datetime object with UTC timezone.

        Returns:
            datetime with UTC timezone from the given string.

        """
        return dateutil.parser.parse(unicode(datetime_str)).replace(tzinfo=pytz.utc)

    def add_xml_to_node(self, node):
        """
        Serialize the XBlock to XML for exporting.
        """
        serialize_content_to_xml(self, node)

    def render_error(self, error_msg):
        """
        Render an error message.

        Args:
            error_msg (unicode): The error message to display.

        Returns:
            Response: A response object with an HTML body.
        """
        context = Context({'error_msg': error_msg})
        template = get_template('openassessmentblock/oa_error.html')
        return Response(template.render(context), content_type='application/html', charset='UTF-8')

    def is_closed(self, step=None):
        """
        Checks if the question is closed.

        Determines if the start date is in the future or the end date has
            passed.  Optionally limited to a particular step in the workflow.

        Kwargs:
            step (str): The step in the workflow to check.  Options are:
                None: check whether the problem as a whole is open.
                "submission": check whether the submission section is open.
                "peer-assessment": check whether the peer-assessment section is open.
                "self-assessment": check whether the self-assessment section is open.

        Returns:
            tuple of the form (is_closed, reason, date), where
                is_closed (bool): indicates whether the step is closed.
                reason (str or None): specifies the reason the step is closed ("start" or "due")
                date (datetime or None): is the start/due date.

        Examples:
            >>> is_closed()
            False, None, None
            >>> is_closed(step="submission")
            True, "due", datetime.datetime(2014, 3, 27, 22, 7, 38, 788861)
            >>> is_closed(step="self-assessment")
            True, "start", datetime.datetime(2014, 3, 27, 22, 7, 38, 788861)

        """
        submission_range = (self.start, self.submission_due)
        assessment_ranges = [
            (asmnt.get('start'), asmnt.get('due'))
            for asmnt in self.rubric_assessments
        ]

        # Resolve unspecified dates and date strings to datetimes
        start, due, date_ranges = resolve_dates(self.start, self.due, [submission_range] + assessment_ranges)

        # Based on the step, choose the date range to consider
        # We hard-code this to the submission -> peer -> self workflow for now;
        # later, we can revisit to make this more flexible.
        open_range = (start, due)
        if step == "submission":
            open_range = date_ranges[0]
        if step == "peer-assessment":
            open_range = date_ranges[1]
        if step == "self-assessment":
            open_range = date_ranges[2]

        # Check if we are in the open date range
        now = dt.datetime.now().replace(tzinfo=pytz.utc)

        if now < open_range[0]:
            return True, "start", open_range[0]
        elif now >= open_range[1]:
            return True, "due", open_range[1]
        else:
            return False, None, None

    def is_released(self, step=None):
        """
        Check if a question has been released.

        Kwargs:
            step (str): The step in the workflow to check.
                None: check whether the problem as a whole is open.
                "submission": check whether the submission section is open.
                "peer-assessment": check whether the peer-assessment section is open.
                "self-assessment": check whether the self-assessment section is open.

        Returns:
            bool
        """
        # By default, assume that we're published, in case the runtime doesn't support publish date.
        is_published = getattr(self, 'published_date', True) is not None
        is_closed, reason, __ = self.is_closed(step=step)
        return is_published and (not is_closed or reason == 'due')

    def get_assessment_module(self, mixin_name):
        """
        Get a configured assessment module by name.

        Args:
            mixin_name (str): The name of the mixin (e.g. "self-assessment" or "peer-assessment")

        Returns:
            dict

        Example:
            >>> self.get_assessment_module('peer-assessment')
            {
                "name": "peer-assessment",
                "start": None,
                "due": None,
                "must_grade": 5,
                "must_be_graded_by": 3,
            }
        """
        for assessment in self.rubric_assessments:
            if assessment["name"] == mixin_name:
                return assessment

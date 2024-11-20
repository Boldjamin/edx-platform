"""
Certificates factories
"""


import datetime
from uuid import uuid4

from factory import Sequence
from factory.django import DjangoModelFactory

from common.djangoapps.student.models import LinkedInAddToProfileConfiguration
from lms.djangoapps.certificates.models import (
    CertificateAllowlist,
    CertificateDateOverride,
    CertificateHtmlViewConfiguration,
    CertificateInvalidation,
    CertificateStatuses,
    CertificateTemplate,
    GeneratedCertificate,
)


class GeneratedCertificateFactory(DjangoModelFactory):
    """
    GeneratedCertificate factory
    """

    class Meta:
        model = GeneratedCertificate

    course_id = None
    status = CertificateStatuses.unavailable
    mode = GeneratedCertificate.MODES.honor
    name = ""
    verify_uuid = uuid4().hex
    grade = ""


class CertificateAllowlistFactory(DjangoModelFactory):
    """
    Certificate allowlist factory
    """

    class Meta:
        model = CertificateAllowlist

    course_id = None
    allowlist = True
    notes = "Test Notes"


class CertificateInvalidationFactory(DjangoModelFactory):
    """
    CertificateInvalidation factory
    """

    class Meta:
        model = CertificateInvalidation

    notes = "Test Notes"
    active = True


class CertificateHtmlViewConfigurationFactory(DjangoModelFactory):
    """
    CertificateHtmlViewConfiguration factory
    """

    class Meta:
        model = CertificateHtmlViewConfiguration

    enabled = True
    configuration = {
        "default": {
            "accomplishment_class_append": "accomplishment-certificate",
            "platform_name": "Your Platform Name Here",
            "company_about_url": "http://www.example.com/about-us",
            "company_privacy_url": "http://www.example.com/privacy-policy",
            "company_tos_url": "http://www.example.com/terms-service",
            "company_verified_certificate_url": "http://www.example.com/verified-certificate",
            "logo_src": "/static/certificates/images/logo.png",
            "logo_url": "http://www.example.com"
        },
        "honor": {
            "certificateTitle": "Certificate of Achievement",
            "certificateType": "Honor Code Factory",
            "documentBodyClassAppend": "is-honorcode"
        },
        "verified": {
            "certificate_type": "Verified",
            "certificate_title": "Verified Certificate of Achievement"
        }
    }


class LinkedInAddToProfileConfigurationFactory(DjangoModelFactory):
    """
    LinkedInAddToProfileConfiguration factory
    """

    class Meta:
        model = LinkedInAddToProfileConfiguration

    enabled = True
    company_identifier = "1337"


class CertificateDateOverrideFactory(DjangoModelFactory):
    """
    CertificateDateOverride factory
    """

    class Meta:
        model = CertificateDateOverride

    date = datetime.datetime(2021, 5, 11, 0, 0, tzinfo=datetime.timezone.utc)
    reason = "Learner really wanted this on their birthday"


class CertificateTemplateFactory(DjangoModelFactory):
    """CertificateTemplate factory"""

    class Meta:
        model = CertificateTemplate

    name = Sequence("template{}".format)
    description = Sequence("description for template{}".format)
    template = ""
    is_active = True

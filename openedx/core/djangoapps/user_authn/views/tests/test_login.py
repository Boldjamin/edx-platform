# coding:utf-8
"""
Tests for student activation and login
"""
from __future__ import absolute_import

import json
import unicodedata

import ddt
import six
from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseBadRequest
from django.test.client import Client
from django.test.utils import override_settings
from django.urls import NoReverseMatch, reverse
from mock import patch
from six.moves import range

from openedx.core.djangoapps.password_policy.compliance import (
    NonCompliantPasswordException,
    NonCompliantPasswordWarning
)
from openedx.core.djangoapps.user_api.config.waffle import PREVENT_AUTH_USER_WRITES, waffle
from openedx.core.djangoapps.user_authn.cookies import jwt_cookies
from openedx.core.djangoapps.user_authn.tests.utils import setup_login_oauth_client
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase
from student.tests.factories import RegistrationFactory, UserFactory, UserProfileFactory


@ddt.ddt
class LoginTest(CacheIsolationTestCase):
    """
    Test login_user() view
    """

    ENABLED_CACHES = ['default']
    LOGIN_FAILED_WARNING = 'Email or password is incorrect'
    ACTIVATE_ACCOUNT_WARNING = 'In order to sign in, you need to activate your account'
    username = 'test'
    user_email = 'test@edx.org'
    password = 'test_password'

    def setUp(self):
        """Setup a test user along with its registration and profile"""
        super(LoginTest, self).setUp()
        self.user = UserFactory.build(username=self.username, email=self.user_email)
        self.user.set_password(self.password)
        self.user.save()

        RegistrationFactory(user=self.user)
        UserProfileFactory(user=self.user)

        self.client = Client()
        cache.clear()

        try:
            self.url = reverse('login_post')
        except NoReverseMatch:
            self.url = reverse('login')

    def test_login_success(self):
        response, mock_audit_log = self._login_response(
            self.user_email, self.password, patched_audit_log='student.models.AUDIT_LOG'
        )
        self._assert_response(response, success=True)
        self._assert_audit_log(mock_audit_log, 'info', [u'Login success', self.user_email])

    @patch.dict("django.conf.settings.FEATURES", {'SQUELCH_PII_IN_LOGS': True})
    def test_login_success_no_pii(self):
        response, mock_audit_log = self._login_response(
            self.user_email, self.password, patched_audit_log='student.models.AUDIT_LOG'
        )
        self._assert_response(response, success=True)
        self._assert_audit_log(mock_audit_log, 'info', [u'Login success'])
        self._assert_not_in_audit_log(mock_audit_log, 'info', [self.user_email])

    def test_login_success_unicode_email(self):
        unicode_email = u'test' + six.unichr(40960) + u'@edx.org'
        self.user.email = unicode_email
        self.user.save()

        response, mock_audit_log = self._login_response(
            unicode_email, self.password, patched_audit_log='student.models.AUDIT_LOG'
        )
        self._assert_response(response, success=True)
        self._assert_audit_log(mock_audit_log, 'info', [u'Login success', unicode_email])

    def test_last_login_updated(self):
        old_last_login = self.user.last_login
        self.test_login_success()
        self.user.refresh_from_db()
        assert self.user.last_login > old_last_login

    def test_login_success_prevent_auth_user_writes(self):
        with waffle().override(PREVENT_AUTH_USER_WRITES, True):
            old_last_login = self.user.last_login
            self.test_login_success()
            self.user.refresh_from_db()
            assert old_last_login == self.user.last_login

    def test_login_fail_no_user_exists(self):
        nonexistent_email = u'not_a_user@edx.org'
        response, mock_audit_log = self._login_response(
            nonexistent_email,
            self.password,
        )
        self._assert_response(response, success=False, value=self.LOGIN_FAILED_WARNING)
        self._assert_audit_log(mock_audit_log, 'warning', [u'Login failed', u'Unknown user email', nonexistent_email])

    @patch.dict("django.conf.settings.FEATURES", {'SQUELCH_PII_IN_LOGS': True})
    def test_login_fail_no_user_exists_no_pii(self):
        nonexistent_email = u'not_a_user@edx.org'
        response, mock_audit_log = self._login_response(
            nonexistent_email,
            self.password,
        )
        self._assert_response(response, success=False, value=self.LOGIN_FAILED_WARNING)
        self._assert_audit_log(mock_audit_log, 'warning', [u'Login failed', u'Unknown user email'])
        self._assert_not_in_audit_log(mock_audit_log, 'warning', [nonexistent_email])

    def test_login_fail_wrong_password(self):
        response, mock_audit_log = self._login_response(
            self.user_email,
            'wrong_password',
        )
        self._assert_response(response, success=False, value=self.LOGIN_FAILED_WARNING)
        self._assert_audit_log(mock_audit_log, 'warning',
                               [u'Login failed', u'password for', self.user_email, u'invalid'])

    @patch.dict("django.conf.settings.FEATURES", {'SQUELCH_PII_IN_LOGS': True})
    def test_login_fail_wrong_password_no_pii(self):
        response, mock_audit_log = self._login_response(self.user_email, 'wrong_password')
        self._assert_response(response, success=False, value=self.LOGIN_FAILED_WARNING)
        self._assert_audit_log(mock_audit_log, 'warning', [u'Login failed', u'password for', u'invalid'])
        self._assert_not_in_audit_log(mock_audit_log, 'warning', [self.user_email])

    @patch.dict("django.conf.settings.FEATURES", {'SQUELCH_PII_IN_LOGS': True})
    def test_login_not_activated_no_pii(self):
        # De-activate the user
        self.user.is_active = False
        self.user.save()

        # Should now be unable to login
        response, mock_audit_log = self._login_response(
            self.user_email,
            self.password
        )
        self._assert_response(response, success=False,
                              value="In order to sign in, you need to activate your account.")
        self._assert_audit_log(mock_audit_log, 'warning', [u'Login failed', u'Account not active for user'])
        self._assert_not_in_audit_log(mock_audit_log, 'warning', [u'test'])

    def test_login_not_activated_with_correct_credentials(self):
        """
        Tests that when user login with the correct credentials but with an inactive
        account, the system, send account activation email notification to the user.
        """
        self.user.is_active = False
        self.user.save()

        response, mock_audit_log = self._login_response(
            self.user_email,
            self.password,
        )
        self._assert_response(response, success=False, value=self.ACTIVATE_ACCOUNT_WARNING)
        self._assert_audit_log(mock_audit_log, 'warning', [u'Login failed', u'Account not active for user'])

    @patch('openedx.core.djangoapps.user_authn.views.login._log_and_raise_inactive_user_auth_error')
    def test_login_inactivated_user_with_incorrect_credentials(self, mock_inactive_user_email_and_error):
        """
        Tests that when user login with incorrect credentials and an inactive account,
        the system does *not* send account activation email notification to the user.
        """
        nonexistent_email = 'incorrect@email.com'
        self.user.is_active = False
        self.user.save()
        response, mock_audit_log = self._login_response(nonexistent_email, 'incorrect_password')

        self.assertFalse(mock_inactive_user_email_and_error.called)
        self._assert_response(response, success=False, value=self.LOGIN_FAILED_WARNING)
        self._assert_audit_log(mock_audit_log, 'warning', [u'Login failed', u'Unknown user email', nonexistent_email])

    def test_login_unicode_email(self):
        unicode_email = self.user_email + six.unichr(40960)
        response, mock_audit_log = self._login_response(
            unicode_email,
            self.password,
        )
        self._assert_response(response, success=False)
        self._assert_audit_log(mock_audit_log, 'warning', [u'Login failed', unicode_email])

    def test_login_unicode_password(self):
        unicode_password = self.password + six.unichr(1972)
        response, mock_audit_log = self._login_response(
            self.user_email,
            unicode_password,
        )
        self._assert_response(response, success=False)
        self._assert_audit_log(mock_audit_log, 'warning',
                               [u'Login failed', u'password for', self.user_email, u'invalid'])

    def test_logout_logging(self):
        response, _ = self._login_response(self.user_email, self.password)
        self._assert_response(response, success=True)
        logout_url = reverse('logout')
        with patch('student.models.AUDIT_LOG') as mock_audit_log:
            response = self.client.post(logout_url)
        self.assertEqual(response.status_code, 200)
        self._assert_audit_log(mock_audit_log, 'info', [u'Logout', u'test'])

    def test_login_user_info_cookie(self):
        response, _ = self._login_response(self.user_email, self.password)
        self._assert_response(response, success=True)

        # Verify the format of the "user info" cookie set on login
        cookie = self.client.cookies[settings.EDXMKTG_USER_INFO_COOKIE_NAME]
        user_info = json.loads(cookie.value)

        self.assertEqual(user_info["version"], settings.EDXMKTG_USER_INFO_COOKIE_VERSION)
        self.assertEqual(user_info["username"], self.user.username)

        # Check that the URLs are absolute
        for url in user_info["header_urls"].values():
            self.assertIn("http://testserver/", url)

    def test_logout_deletes_mktg_cookies(self):
        response, _ = self._login_response(self.user_email, self.password)
        self._assert_response(response, success=True)

        # Check that the marketing site cookies have been set
        self.assertIn(settings.EDXMKTG_LOGGED_IN_COOKIE_NAME, self.client.cookies)
        self.assertIn(settings.EDXMKTG_USER_INFO_COOKIE_NAME, self.client.cookies)

        # Log out
        logout_url = reverse('logout')
        response = self.client.post(logout_url)

        # Check that the marketing site cookies have been deleted
        # (cookies are deleted by setting an expiration date in 1970)
        for cookie_name in [settings.EDXMKTG_LOGGED_IN_COOKIE_NAME, settings.EDXMKTG_USER_INFO_COOKIE_NAME]:
            cookie = self.client.cookies[cookie_name]
            self.assertIn("01-Jan-1970", cookie.get('expires'))

    @override_settings(
        EDXMKTG_LOGGED_IN_COOKIE_NAME=u"unicode-logged-in",
        EDXMKTG_USER_INFO_COOKIE_NAME=u"unicode-user-info",
    )
    def test_unicode_mktg_cookie_names(self):
        # When logged in cookie names are loaded from JSON files, they may
        # have type `unicode` instead of `str`, which can cause errors
        # when calling Django cookie manipulation functions.
        response, _ = self._login_response(self.user_email, self.password)
        self._assert_response(response, success=True)

        response = self.client.post(reverse('logout'))
        expected = {
            'target': '/',
        }
        self.assertDictContainsSubset(expected, response.context_data)

    @patch.dict("django.conf.settings.FEATURES", {'SQUELCH_PII_IN_LOGS': True})
    def test_logout_logging_no_pii(self):
        response, _ = self._login_response(self.user_email, self.password)
        self._assert_response(response, success=True)
        logout_url = reverse('logout')
        with patch('student.models.AUDIT_LOG') as mock_audit_log:
            response = self.client.post(logout_url)
        self.assertEqual(response.status_code, 200)
        self._assert_audit_log(mock_audit_log, 'info', [u'Logout'])
        self._assert_not_in_audit_log(mock_audit_log, 'info', [u'test'])

    def test_login_ratelimited_success(self):
        # Try (and fail) logging in with fewer attempts than the limit of 30
        # and verify that you can still successfully log in afterwards.
        for i in range(20):
            password = u'test_password{0}'.format(i)
            response, _audit_log = self._login_response(self.user_email, password)
            self._assert_response(response, success=False)
        # now try logging in with a valid password
        response, _audit_log = self._login_response(self.user_email, self.password)
        self._assert_response(response, success=True)

    def test_login_ratelimited(self):
        # try logging in 30 times, the default limit in the number of failed
        # login attempts in one 5 minute period before the rate gets limited
        for i in range(30):
            password = u'test_password{0}'.format(i)
            self._login_response(self.user_email, password)
        # check to see if this response indicates that this was ratelimited
        response, _audit_log = self._login_response(self.user_email, 'wrong_password')
        self._assert_response(response, success=False, value='Too many failed login attempts')

    @patch.dict("django.conf.settings.FEATURES", {"DISABLE_SET_JWT_COOKIES_FOR_TESTS": False})
    def test_login_refresh(self):
        def _assert_jwt_cookie_present(response):
            self.assertEqual(response.status_code, 200)
            self.assertIn(jwt_cookies.jwt_cookie_header_payload_name(), self.client.cookies)

        setup_login_oauth_client()
        response, _ = self._login_response(self.user_email, self.password)
        _assert_jwt_cookie_present(response)

        response = self.client.post(reverse('login_refresh'))
        _assert_jwt_cookie_present(response)

    @patch.dict("django.conf.settings.FEATURES", {"DISABLE_SET_JWT_COOKIES_FOR_TESTS": False})
    def test_login_refresh_anonymous_user(self):
        response = self.client.post(reverse('login_refresh'))
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(jwt_cookies.jwt_cookie_header_payload_name(), self.client.cookies)

    @patch.dict("django.conf.settings.FEATURES", {'PREVENT_CONCURRENT_LOGINS': True})
    def test_single_session(self):
        creds = {'email': self.user_email, 'password': self.password}
        client1 = Client()
        client2 = Client()

        response = client1.post(self.url, creds)
        self._assert_response(response, success=True)

        # Reload the user from the database
        self.user = User.objects.get(pk=self.user.pk)

        self.assertEqual(self.user.profile.get_meta()['session_id'], client1.session.session_key)

        # second login should log out the first
        response = client2.post(self.url, creds)
        self._assert_response(response, success=True)

        try:
            # this test can be run with either lms or studio settings
            # since studio does not have a dashboard url, we should
            # look for another url that is login_required, in that case
            url = reverse('dashboard')
        except NoReverseMatch:
            url = reverse('upload_transcripts')
        response = client1.get(url)
        # client1 will be logged out
        self.assertEqual(response.status_code, 302)

    @patch.dict("django.conf.settings.FEATURES", {'PREVENT_CONCURRENT_LOGINS': True})
    def test_single_session_with_no_user_profile(self):
        """
        Assert that user login with cas (Central Authentication Service) is
        redirect to dashboard in case of lms or upload_transcripts in case of
        cms
        """
        user = UserFactory.build(username='tester', email='tester@edx.org')
        user.set_password(self.password)
        user.save()

        # Assert that no profile is created.
        self.assertFalse(hasattr(user, 'profile'))

        creds = {'email': 'tester@edx.org', 'password': self.password}
        client1 = Client()
        client2 = Client()

        response = client1.post(self.url, creds)
        self._assert_response(response, success=True)

        # Reload the user from the database
        user = User.objects.get(pk=user.pk)

        # Assert that profile is created.
        self.assertTrue(hasattr(user, 'profile'))

        # second login should log out the first
        response = client2.post(self.url, creds)
        self._assert_response(response, success=True)

        try:
            # this test can be run with either lms or studio settings
            # since studio does not have a dashboard url, we should
            # look for another url that is login_required, in that case
            url = reverse('dashboard')
        except NoReverseMatch:
            url = reverse('upload_transcripts')
        response = client1.get(url)
        # client1 will be logged out
        self.assertEqual(response.status_code, 302)

    @patch.dict("django.conf.settings.FEATURES", {'PREVENT_CONCURRENT_LOGINS': True})
    def test_single_session_with_url_not_having_login_required_decorator(self):
        # accessing logout url as it does not have login-required decorator it will avoid redirect
        # and go inside the enforce_single_login

        creds = {'email': self.user_email, 'password': self.password}
        client1 = Client()
        client2 = Client()

        response = client1.post(self.url, creds)
        self._assert_response(response, success=True)

        # Reload the user from the database
        self.user = User.objects.get(pk=self.user.pk)

        self.assertEqual(self.user.profile.get_meta()['session_id'], client1.session.session_key)

        # second login should log out the first
        response = client2.post(self.url, creds)
        self._assert_response(response, success=True)

        url = reverse('logout')

        response = client1.get(url)
        self.assertEqual(response.status_code, 200)

    def test_change_enrollment_400(self):
        """
        Tests that a 400 in change_enrollment doesn't lead to a 404
        and in fact just logs in the user without incident
        """
        # add this post param to trigger a call to change_enrollment
        extra_post_params = {"enrollment_action": "enroll"}
        with patch('student.views.change_enrollment') as mock_change_enrollment:
            mock_change_enrollment.return_value = HttpResponseBadRequest("I am a 400")
            response, _ = self._login_response(
                self.user_email, self.password, extra_post_params=extra_post_params,
            )
        response_content = json.loads(response.content.decode('utf-8'))
        self.assertIsNone(response_content["redirect_url"])
        self._assert_response(response, success=True)

    def test_change_enrollment_200_no_redirect(self):
        """
        Tests "redirect_url" is None if change_enrollment returns a HttpResponse
        with no content
        """
        # add this post param to trigger a call to change_enrollment
        extra_post_params = {"enrollment_action": "enroll"}
        with patch('student.views.change_enrollment') as mock_change_enrollment:
            mock_change_enrollment.return_value = HttpResponse()
            response, _ = self._login_response(
                self.user_email, self.password, extra_post_params=extra_post_params,
            )
        response_content = json.loads(response.content.decode('utf-8'))
        self.assertIsNone(response_content["redirect_url"])
        self._assert_response(response, success=True)

    @override_settings(PASSWORD_POLICY_COMPLIANCE_ROLLOUT_CONFIG={'ENFORCE_COMPLIANCE_ON_LOGIN': True})
    def test_check_password_policy_compliance(self):
        """
        Tests _enforce_password_policy_compliance succeeds when no exception is thrown
        """
        enforce_compliance_path = 'openedx.core.djangoapps.password_policy.compliance.enforce_compliance_on_login'
        with patch(enforce_compliance_path) as mock_check_password_policy_compliance:
            mock_check_password_policy_compliance.return_value = HttpResponse()
            response, _ = self._login_response(self.user_email, self.password)
            response_content = json.loads(response.content.decode('utf-8'))
        self.assertTrue(response_content.get('success'))

    @override_settings(PASSWORD_POLICY_COMPLIANCE_ROLLOUT_CONFIG={'ENFORCE_COMPLIANCE_ON_LOGIN': True})
    def test_check_password_policy_compliance_exception(self):
        """
        Tests _enforce_password_policy_compliance fails with an exception thrown
        """
        enforce_compliance_on_login = 'openedx.core.djangoapps.password_policy.compliance.enforce_compliance_on_login'
        with patch(enforce_compliance_on_login) as mock_enforce_compliance_on_login:
            mock_enforce_compliance_on_login.side_effect = NonCompliantPasswordException()
            response, _ = self._login_response(
                self.user_email,
                self.password
            )
            response_content = json.loads(response.content.decode('utf-8'))
        self.assertFalse(response_content.get('success'))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Password reset', mail.outbox[0].subject)

    @override_settings(PASSWORD_POLICY_COMPLIANCE_ROLLOUT_CONFIG={'ENFORCE_COMPLIANCE_ON_LOGIN': True})
    def test_check_password_policy_compliance_warning(self):
        """
        Tests _enforce_password_policy_compliance succeeds with a warning thrown
        """
        enforce_compliance_on_login = 'openedx.core.djangoapps.password_policy.compliance.enforce_compliance_on_login'
        with patch(enforce_compliance_on_login) as mock_enforce_compliance_on_login:
            mock_enforce_compliance_on_login.side_effect = NonCompliantPasswordWarning('Test warning')
            response, _ = self._login_response(self.user_email, self.password)
            response_content = json.loads(response.content.decode('utf-8'))
            self.assertIn('Test warning', self.client.session['_messages'])
        self.assertTrue(response_content.get('success'))

    @ddt.data(
        ('test_password', 'test_password', True),
        (unicodedata.normalize('NFKD', u'Ṗŕệṿïệẅ Ṯệẍt'),
         unicodedata.normalize('NFKC', u'Ṗŕệṿïệẅ Ṯệẍt'), False),
        (unicodedata.normalize('NFKC', u'Ṗŕệṿïệẅ Ṯệẍt'),
         unicodedata.normalize('NFKD', u'Ṗŕệṿïệẅ Ṯệẍt'), True),
        (unicodedata.normalize('NFKD', u'Ṗŕệṿïệẅ Ṯệẍt'),
         unicodedata.normalize('NFKD', u'Ṗŕệṿïệẅ Ṯệẍt'), False),
    )
    @ddt.unpack
    def test_password_unicode_normalization_login(self, password, password_entered, login_success):
        """
        Tests unicode normalization on user's passwords on login.
        """
        self.user.set_password(password)
        self.user.save()
        response, _ = self._login_response(self.user.email, password_entered)
        self._assert_response(response, success=login_success)

    def _login_response(self, email, password, patched_audit_log=None, extra_post_params=None):
        """
        Post the login info
        """
        if patched_audit_log is None:
            patched_audit_log = 'openedx.core.djangoapps.user_authn.views.login.AUDIT_LOG'
        post_params = {'email': email, 'password': password}
        if extra_post_params is not None:
            post_params.update(extra_post_params)
        with patch(patched_audit_log) as mock_audit_log:
            result = self.client.post(self.url, post_params)
        return result, mock_audit_log

    def _assert_response(self, response, success=None, value=None):
        """
        Assert that the response had status 200 and returned a valid
        JSON-parseable dict.

        If success is provided, assert that the response had that
        value for 'success' in the JSON dict.

        If value is provided, assert that the response contained that
        value for 'value' in the JSON dict.
        """
        self.assertEqual(response.status_code, 200)

        try:
            response_dict = json.loads(response.content.decode('utf-8'))
        except ValueError:
            self.fail(u"Could not parse response content as JSON: %s"
                      % str(response.content))

        if success is not None:
            self.assertEqual(response_dict['success'], success)

        if value is not None:
            msg = (u"'%s' did not contain '%s'" %
                   (six.text_type(response_dict['value']), six.text_type(value)))
            self.assertIn(value, response_dict['value'], msg)

    def _assert_audit_log(self, mock_audit_log, level, log_strings):
        """
        Check that the audit log has received the expected call as its last call.
        """
        method_calls = mock_audit_log.method_calls
        name, args, _kwargs = method_calls[-1]
        self.assertEquals(name, level)
        self.assertEquals(len(args), 1)
        format_string = args[0]
        for log_string in log_strings:
            self.assertIn(log_string, format_string)

    def _assert_not_in_audit_log(self, mock_audit_log, level, log_strings):
        """
        Check that the audit log has received the expected call as its last call.
        """
        method_calls = mock_audit_log.method_calls
        name, args, _kwargs = method_calls[-1]
        self.assertEquals(name, level)
        self.assertEquals(len(args), 1)
        format_string = args[0]
        for log_string in log_strings:
            self.assertNotIn(log_string, format_string)

# Copyright 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests the OrgRulesEngine."""

import copy
import itertools
import mock
import yaml

from google.apputils import basetest
from google.cloud.security.common.gcp_type.iam_policy import IamPolicyBinding
from google.cloud.security.common.gcp_type.iam_policy import IamPolicyMember
from google.cloud.security.common.gcp_type.organization import Organization
from google.cloud.security.common.gcp_type.project import Project
from google.cloud.security.common.util import file_loader
from google.cloud.security.scanner.audit.errors import InvalidRulesSchemaError
from google.cloud.security.scanner.audit.org_rules_engine import OrgRuleBook
from google.cloud.security.scanner.audit.org_rules_engine import OrgRulesEngine
from google.cloud.security.scanner.audit.org_rules_engine import ResourceRules
from google.cloud.security.scanner.audit.org_rules_engine import Rule
from google.cloud.security.scanner.audit.org_rules_engine import RuleMode
from google.cloud.security.scanner.audit.org_rules_engine import RuleViolation
from google.cloud.security.scanner.audit.org_rules_engine import RULE_VIOLATION_TYPE
from tests.unittest_utils import get_datafile_path
from tests.scanner.data import test_rules


class OrgRulesEngineTest(basetest.TestCase):
    """Tests for the OrgRulesEngine."""

    def setUp(self):
        """Set up."""
        self.org789 = Organization('778899', org_name='My org')
        self.project1 = Project(
            'my-project-1', 12345,
            project_name='My project 1',
            parent=self.org789)
        self.project2 = Project('my-project-2', 12346,
            project_name='My project 2')

    def test_build_rule_book_from_local_yaml_file_works(self):
        """Test that a RuleBook is built correctly with a yaml file."""
        rules_local_path = get_datafile_path(__file__, 'test_rules_1.yaml')
        rules_engine = OrgRulesEngine(rules_file_path=rules_local_path)
        rules_engine.build_rule_book()
        self.assertEqual(4, len(rules_engine.rule_book.resource_rules_map))

    def test_build_rule_book_from_local_json_file_works(self):
        """Test that a RuleBook is built correctly with a json file."""
        rules_local_path = get_datafile_path(__file__, 'test_rules_1.json')
        rules_engine = OrgRulesEngine(rules_file_path=rules_local_path)
        rules_engine.build_rule_book()
        self.assertEqual(4, len(rules_engine.rule_book.resource_rules_map))

    @mock.patch.object(file_loader,
                       '_read_file_from_gcs', autospec=True)
    def test_build_rule_book_from_gcs_works(self, mock_load_rules_from_gcs):
        """Test that a RuleBook is built correctly with a mocked gcs file.

        Setup:
            * Create a mocked GCS object from a test yaml file.
            * Get the yaml file content.

        Expected results:
            There are 4 resources that have rules, in the rule book.
        """
        bucket_name = 'bucket-name'
        rules_path = 'input/test_rules_1.yaml'
        full_rules_path = 'gs://{}/{}'.format(bucket_name, rules_path)
        rules_engine = OrgRulesEngine(rules_file_path=full_rules_path)

        # Read in the rules file
        file_content = None
        with open(get_datafile_path(__file__, 'test_rules_1.yaml'),
                  'r') as rules_local_file:
            try:
                file_content = yaml.safe_load(rules_local_file)
            except yaml.YAMLError:
                raise

        mock_load_rules_from_gcs.return_value = file_content

        rules_engine.build_rule_book()
        self.assertEqual(4, len(rules_engine.rule_book.resource_rules_map))

    def test_build_rule_book_no_resource_type_fails(self):
        """Test that a rule without a resource type cannot be created."""
        rules_local_path = get_datafile_path(__file__, 'test_rules_2.yaml')
        rules_engine = OrgRulesEngine(rules_file_path=rules_local_path)
        with self.assertRaises(InvalidRulesSchemaError):
            rules_engine.build_rule_book()

    def test_add_single_rule_builds_correct_map(self):
        """Test that adding a single rule builds the correct map."""
        rule_book = OrgRuleBook(test_rules.RULES1)
        actual_rules = rule_book.resource_rules_map

        # expected
        rule_bindings = [{
            'role': 'roles/*', 'members': ['user:*@company.com']
        }]
        rule = Rule('my rule', 0,
            [IamPolicyBinding.create_from(b) for b in rule_bindings],
            mode='whitelist')
        expected_org_rules = ResourceRules(self.org789,
                                           rules=set([rule]),
                                           applies_to='self_and_children')
        expected_proj1_rules = ResourceRules(self.project1,
                                             rules=set([rule]),
                                             applies_to='self')
        expected_proj2_rules = ResourceRules(self.project2,
                                             rules=set([rule]),
                                             applies_to='self')
        expected_rules = {
            (self.org789, 'self_and_children'): expected_org_rules,
            (self.project1, 'self'): expected_proj1_rules,
            (self.project2, 'self'): expected_proj2_rules
        }

        self.assertEqual(expected_rules, actual_rules)

    def test_invalid_rule_mode_raises_when_verify_mode(self):
        """Test that an invalid rule mode raises error."""
        with self.assertRaises(InvalidRulesSchemaError):
            RuleMode.verify('nonexistent mode')

    def test_invalid_rule_mode_raises_when_create_rule(self):
        """Test that creating a Rule with invalid rule mode raises error."""
        with self.assertRaises(InvalidRulesSchemaError):
            Rule('exception', 0, [])

    def test_policy_binding_matches_whitelist_rules(self):
        """Test that a policy binding matches the whitelist rules.

        Setup:
            * Create a test policy binding.
            * Create a test rule binding.
            * Create a whitelist rule with the test rules.

        Expected results:
            All policy binding members are in the whitelist.
        """
        test_binding = {
            'role': 'roles/owner',
            'members': [
                'user:foo@company.com',
                'user:abc@def.somewhere.com',
                'group:some-group@googlegroups.com',
                'serviceAccount:12345@iam.gserviceaccount.com',
            ]
        }
        rule_bindings = [
            {
                'role': 'roles/owner',
                'members': [
                    'user:*@company.com',
                    'user:abc@*.somewhere.com',
                    'group:*@googlegroups.com',
                    'serviceAccount:*@*.gserviceaccount.com',
                ]
            }
        ]

        rule = Rule('test rule', 0,
            [IamPolicyBinding.create_from(b) for b in rule_bindings],
            mode='whitelist')
        resource_rule = ResourceRules(rules=[rule])
        results = list(resource_rule.find_mismatches(
            self.project1, test_binding))

        self.assertEqual(0, len(results))

    def test_policy_binding_does_not_match_blacklist_rules(self):
        """Test that a policy binding does not match the blacklist.

        Setup:
            * Create a test policy binding.
            * Create a test rule binding.
            * Create a blacklist rule with the test rules.

        Expected results:
            No policy bindings found in the blacklist.
        """
        test_binding = {
            'role': 'roles/owner',
            'members': [
                'user:someone@notcompany.com',
            ]
        }
        rule_bindings = [
            {
                'role': 'roles/owner',
                'members': [
                    'user:*@company.com',
                    'user:abc@*.somewhere.com',
                    'group:*@googlegroups.com',
                    'serviceAccount:*@*.gserviceaccount.com',
                ]
            }
        ]

        rule = Rule('test rule', 0,
            [IamPolicyBinding.create_from(b) for b in rule_bindings],
            mode='blacklist')
        resource_rule = ResourceRules(rules=[rule])
        results = list(resource_rule.find_mismatches(
            self.project1, test_binding))

        self.assertEqual(0, len(results))

    def test_policy_binding_matches_required_rules(self):
        """Test that a required list of members are found in policy binding.

        Setup:
            * Create a test policy binding.
            * Create a test rule binding.
            * Create a required rule with the test rules.

        Expected results:
            All required members are found in the policy.
        """
        test_binding = {
            'role': 'roles/owner',
            'members': [
                'user:foo@company.com',
                'user:abc@def.somewhere.com',
                'group:some-group@googlegroups.com',
                'serviceAccount:12345@iam.gserviceaccount.com',
            ]
        }
        rule_bindings = [
            {
                'role': 'roles/owner',
                'members': [
                    'user:foo@company.com',
                    'user:abc@def.somewhere.com',
                ]
            }
        ]

        rule = Rule('test rule', 0,
            [IamPolicyBinding.create_from(b) for b in rule_bindings],
            mode='required')
        resource_rule = ResourceRules(rules=[rule])
        results = list(resource_rule.find_mismatches(
            self.project1, test_binding))

        self.assertEqual(0, len(results))

    def test_policy_binding_mismatches_required_rules(self):
        """Test that a required list of members mismatches policy binding.

        Setup:
            * Create a test policy binding.
            * Create a test rule binding.
            * Create a required rule with the test rules.

        Expected results:
            All required members are found in the policy.
        """
        test_binding = {
            'role': 'roles/owner',
            'members': [
                'user:foo@company.com.abc',
                'group:some-group@googlegroups.com',
                'serviceAccount:12345@iam.gserviceaccount.com',
            ]
        }
        rule_bindings = [
            {
                'role': 'roles/owner',
                'members': [
                    'user:foo@company.com',
                    'user:abc@def.somewhere.com',
                ]
            }
        ]

        rule = Rule('test rule', 0,
            [IamPolicyBinding.create_from(b) for b in rule_bindings],
            mode='required')
        resource_rule = ResourceRules(resource=self.project1)
        resource_rule.rules.add(rule)
        results = list(resource_rule.find_mismatches(
            self.project1, test_binding))

        self.assertEqual(1, len(results))

    def test_one_member_mismatch(self):
        """Test a policy where one member mismatches the whitelist.

        Setup:
            * Create a RulesEngine and add test_rules.RULES1.
            * Create the policy binding.
            * Create the Rule and rule bindings.
            * Create the resource association for the Rule.

        Expected results:
            One policy binding member missing from the whitelist.
        """
        # actual
        rules_local_path = get_datafile_path(__file__, 'test_rules_1.yaml')
        rules_engine = OrgRulesEngine(rules_local_path)
        rules_engine.rule_book = OrgRuleBook(test_rules.RULES1)

        policy = {
            'bindings': [{
                'role': 'roles/editor',
                'members': ['user:abc@company.com', 'user:def@goggle.com']
            }]}

        actual_violations = set(rules_engine.find_policy_violations(
            self.project1, policy))

        # expected
        rule_bindings = [{
            'role': 'roles/*',
            'members': ['user:*@company.com']
        }]
        rule = Rule('my rule', 0,
            [IamPolicyBinding.create_from(b) for b in rule_bindings],
            mode='whitelist')
        expected_outstanding = {
            'roles/editor': [
                IamPolicyMember.create_from('user:def@goggle.com')
            ]
        }
        expected_violations = set([
            RuleViolation(
                resource_type=self.project1.resource_type,
                resource_id=self.project1.resource_id,
                rule_name=rule.rule_name,
                rule_index=rule.rule_index,
                role='roles/editor',
                violation_type=RULE_VIOLATION_TYPE.get(rule.mode),
                members=tuple(expected_outstanding['roles/editor']))
        ])

        self.assertEqual(expected_violations, actual_violations)

    def test_no_mismatch(self):
        """Test a policy where no members mismatch the whitelist.

        Setup:
            * Create a RulesEngine and add test_rules.RULES1.
            * Create the policy binding.
            * Create the Rule and rule bindings.
            * Create the resource association for the Rule.

        Expected results:
            No policy binding members missing from the whitelist.
        """
        # actual
        rules_local_path = get_datafile_path(__file__, 'test_rules_1.yaml')
        rules_engine = OrgRulesEngine(rules_local_path)
        rules_engine.rule_book = OrgRuleBook(test_rules.RULES1)
        policy = {
            'bindings': [{
              'role': 'roles/editor',
              'members': ['user:abc@company.com', 'user:def@company.com']
            }]
        }

        actual_violations = set(rules_engine.find_policy_violations(
            self.project1, policy))

        # expected
        expected_violations = set()

        self.assertEqual(expected_violations, actual_violations)

    def test_policy_with_no_rules_has_no_violations(self):
        """Test a policy against an empty RuleBook.

        Setup:
            * Create a Rules Engine
            * Create the policy bindings.
            * Created expected violations list.

        Expected results:
            No policy violations found.
        """
        # actual
        rules_local_path = get_datafile_path(__file__, 'test_rules_1.yaml')
        rules_engine = OrgRulesEngine(rules_local_path)
        rules_engine.rule_book = OrgRuleBook({})
        policy = {
            'bindings': [{
                'role': 'roles/editor',
                'members': ['user:abc@company.com', 'user:def@company.com']
            }]
        }

        actual_violations = set(rules_engine.find_policy_violations(
            self.project1, policy))

        # expected
        expected_violations = set()

        self.assertEqual(expected_violations, actual_violations)

    def test_empty_policy_with_rules_no_violations(self):
        """Test an empty policy against the RulesEngine with rules.

        Setup:
            * Create a RulesEngine.
            * Created expected violations list.

        Expected results:
            No policy violations found.
        """
        # actual
        rules_local_path = get_datafile_path(__file__, 'test_rules_1.yaml')
        rules_engine = OrgRulesEngine(rules_local_path)
        rules_engine.rule_book = OrgRuleBook(test_rules.RULES1)

        actual_violations = set(rules_engine.find_policy_violations(
            self.project1, {}))

        # expected
        expected_violations = set()

        self.assertEqual(expected_violations, actual_violations)

    def test_whitelist_blacklist_rules_vs_policy_has_violations(self):
        """Test a ruleset with whitelist and blacklist violating rules.

        Setup:
            * Create a RulesEngine with RULES2 rule set.
            * Create policy.

        Expected result:
            * Find 1 rule violation.
        """
        # actual
        rules_local_path = get_datafile_path(__file__, 'test_rules_1.yaml')
        rules_engine = OrgRulesEngine(rules_local_path)
        rules_engine.rule_book = OrgRuleBook(test_rules.RULES2)

        policy = {
            'bindings': [
                {
                    'role': 'roles/editor',
                    'members': [
                        'user:baduser@company.com',
                        'user:okuser@company.com',
                        'user:otheruser@other.com'
                    ]
                }
            ]
        }

        actual_violations = set(itertools.chain(
            rules_engine.find_policy_violations(self.project1, policy),
            rules_engine.find_policy_violations(self.project2, policy)))

        # expected
        expected_outstanding1 = {
            'roles/editor': [
                IamPolicyMember.create_from('user:otheruser@other.com')
            ]
        }
        expected_outstanding2 = {
            'roles/editor': [
                IamPolicyMember.create_from('user:baduser@company.com')
            ]
        }

        expected_violations = set([
            RuleViolation(
                rule_index=0,
                rule_name='my rule',
                resource_id=self.project1.resource_id,
                resource_type=self.project1.resource_type,
                violation_type='ADDED',
                role=policy['bindings'][0]['role'],
                members=tuple(expected_outstanding1['roles/editor'])),
            RuleViolation(
                rule_index=0,
                rule_name='my rule',
                resource_type=self.project2.resource_type,
                resource_id=self.project2.resource_id,
                violation_type='ADDED',
                role=policy['bindings'][0]['role'],
                members=tuple(expected_outstanding1['roles/editor'])),
            RuleViolation(
                rule_index=1,
                rule_name='my other rule',
                resource_type=self.project2.resource_type,
                resource_id=self.project2.resource_id,
                violation_type='ADDED',
                role=policy['bindings'][0]['role'],
                members=tuple(expected_outstanding2['roles/editor'])),
            RuleViolation(
                rule_index=2,
                rule_name='required rule',
                resource_id=self.project1.resource_id,
                resource_type=self.project1.resource_type,
                violation_type='REMOVED',
                role='roles/viewer',
                members=tuple([IamPolicyMember.create_from(
                    'user:project_viewer@company.com')]))
        ])

        self.assertItemsEqual(expected_violations, actual_violations)

    def test_org_whitelist_rules_vs_policy_no_violations(self):
        """Test ruleset on an org with whitelist with no rule violations.

        Setup:
            * Create a RulesEngine with RULES1 rule set.
            * Create policy.

        Expected result:
            * Find no rule violations.
        """
        # actual
        rules_local_path = get_datafile_path(__file__, 'test_rules_1.yaml')
        rules_engine = OrgRulesEngine(rules_local_path)
        rules_engine.rule_book = OrgRuleBook(test_rules.RULES1)

        policy = {
            'bindings': [
                {
                    'role': 'roles/editor',
                    'members': [
                        'user:okuser@company.com',
                    ]
                }
            ]
        }

        actual_violations = set(rules_engine.find_policy_violations(
            self.org789, policy))

        self.assertItemsEqual(set(), actual_violations)

    def test_org_proj_rules_vs_policy_has_violations(self):
        """Test rules on org and project with whitelist, blacklist, required.

        Test whitelist, blacklist, and required rules against an org that has
        1 blacklist violation and a project that has 1 whitelist violation and
        1 required violation.

        Setup:
            * Create a RulesEngine with RULES3 rule set.
            * Create policy.

        Expected result:
            * Find 3 rule violations.
        """
        # actual
        rules_local_path = get_datafile_path(__file__, 'test_rules_1.yaml')
        rules_engine = OrgRulesEngine(rules_local_path)
        rules_engine.rule_book = OrgRuleBook(test_rules.RULES3)

        org_policy = {
            'bindings': [
                {
                    'role': 'roles/editor',
                    'members': [
                        'user:okuser@company.com',
                        'user:baduser@company.com',
                    ]
                }
            ]
        }

        project_policy = {
            'bindings': [
                {
                    'role': 'roles/editor',
                    'members': [
                        'user:okuserr2@company.com',
                        'user:user@other.com',
                    ]
                }
            ]
        }

        actual_violations = set(itertools.chain(
            rules_engine.find_policy_violations(self.org789, org_policy),
            rules_engine.find_policy_violations(self.project1, project_policy),
            ))

        # expected
        expected_outstanding_org = {
            'roles/editor': [
                IamPolicyMember.create_from('user:baduser@company.com')
            ]
        }
        expected_outstanding_project = {
            'roles/editor': [
                IamPolicyMember.create_from('user:user@other.com')
            ],
            'roles/viewer': [
                IamPolicyMember.create_from('user:project_viewer@company.com')
            ]
        }

        expected_violations = set([
            RuleViolation(
                rule_index=1,
                rule_name='my blacklist rule',
                resource_id=self.org789.resource_id,
                resource_type=self.org789.resource_type,
                violation_type='ADDED',
                role=org_policy['bindings'][0]['role'],
                members=tuple(expected_outstanding_org['roles/editor'])),
            RuleViolation(
                rule_index=0,
                rule_name='my whitelist rule',
                resource_id=self.project1.resource_id,
                resource_type=self.project1.resource_type,
                violation_type='ADDED',
                role=project_policy['bindings'][0]['role'],
                members=tuple(expected_outstanding_project['roles/editor'])),
            RuleViolation(
                rule_index=2,
                rule_name='my required rule',
                resource_id=self.project1.resource_id,
                resource_type=self.project1.resource_type,
                violation_type='REMOVED',
                role='roles/viewer',
                members=tuple(expected_outstanding_project['roles/viewer'])),
        ])

        self.assertItemsEqual(expected_violations, actual_violations)

    def test_org_self_rules_work_with_org_child_rules(self):
        """Test org "self" whitelist works with org "children" whitelist

        Test hierarchical rules.

        Setup:
            * Create a RulesEngine with RULES4 rule set.
            * Create policy.

        Expected result:
            * Find 3 rule violations.
        """
        # actual
        rules_local_path = get_datafile_path(__file__, 'test_rules_1.yaml')
        rules_engine = OrgRulesEngine(rules_local_path)
        rules_engine.rule_book = OrgRuleBook(test_rules.RULES4)

        org_policy = {
            'bindings': [
                {
                    'role': 'roles/owner',
                    'members': [
                        'user:owner@company.com',
                        'user:baduser@company.com',
                    ]
                }
            ]
        }

        project_policy = {
            'bindings': [
                {
                    'role': 'roles/editor',
                    'members': [
                        'user:okuser2@company.com',
                        'user:user@other.com',
                    ]
                }
            ]
        }

        actual_violations = set(itertools.chain(
            rules_engine.find_policy_violations(self.org789, org_policy),
            rules_engine.find_policy_violations(self.project1, project_policy)))

        # expected
        expected_outstanding_org = {
            'roles/owner': [
                IamPolicyMember.create_from('user:baduser@company.com')
            ]
        }
        expected_outstanding_proj = {
            'roles/editor': [
                IamPolicyMember.create_from('user:user@other.com')
            ]
        }

        expected_violations = set([
            RuleViolation(
                rule_index=0,
                rule_name='org whitelist',
                resource_id=self.org789.resource_id,
                resource_type=self.org789.resource_type,
                violation_type='ADDED',
                role=org_policy['bindings'][0]['role'],
                members=tuple(expected_outstanding_org['roles/owner'])),
            RuleViolation(
                rule_index=1,
                rule_name='project whitelist',
                resource_id=self.project1.resource_id,
                resource_type=self.project1.resource_type,
                violation_type='ADDED',
                role=project_policy['bindings'][0]['role'],
                members=tuple(expected_outstanding_proj['roles/editor'])),
        ])

        self.assertItemsEqual(expected_violations, actual_violations)

    def test_org_project_noinherit_project_overrides_org_rule(self):
        """Test org with blacklist and child with whitelist, no inherit.

        Test that the project whitelist rule overrides the org blacklist rule
        when the project does not inherit from parent.

        Setup:
            * Create a RulesEngine with RULES5 rule set.
            * Create policy.

        Expected result:
            * Find 0 rule violations.
        """
        # actual
        rules_local_path = get_datafile_path(__file__, 'test_rules_1.yaml')
        rules_engine = OrgRulesEngine(rules_local_path)
        rules_engine.rule_book = OrgRuleBook(test_rules.RULES5)

        project_policy = {
            'bindings': [
                {
                    'role': 'roles/owner',
                    'members': [
                        'user:owner@company.com',
                    ]
                }
            ]
        }

        actual_violations = set(itertools.chain(
            rules_engine.find_policy_violations(self.project1, project_policy)
        ))

        # expected
        expected_outstanding_proj = {
            'roles/editor': [
                IamPolicyMember.create_from('user:user@other.com')
            ]
        }

        expected_violations = set([])

        self.assertItemsEqual(expected_violations, actual_violations)

    def test_org_2_child_rules_report_violation(self):
        """Test org "children" whitelist works with org "children" blacklist.

        Test that org children whitelist with org children blacklist rules
        report violation.

        Setup:
            * Create a RulesEngine with RULES6 rule set.
            * Create policy.

        Expected result:
            * Find 1 rule violation.
        """
        # actual
        rules_local_path = get_datafile_path(__file__, 'test_rules_1.yaml')
        rules_engine = OrgRulesEngine(rules_local_path)
        rules_engine.rule_book = OrgRuleBook(test_rules.RULES6)

        project_policy = {
            'bindings': [
                {
                    'role': 'roles/owner',
                    'members': [
                        'user:owner@company.com',
                    ]
                }
            ]
        }

        actual_violations = set(
            rules_engine.find_policy_violations(self.project1, project_policy)
        )

        # expected
        expected_outstanding_proj = {
            'roles/owner': [
                IamPolicyMember.create_from('user:owner@company.com')
            ]
        }

        expected_violations = set([
            RuleViolation(
                rule_index=1,
                rule_name='project blacklist',
                resource_id=self.project1.resource_id,
                resource_type=self.project1.resource_type,
                violation_type='ADDED',
                role=project_policy['bindings'][0]['role'],
                members=tuple(expected_outstanding_proj['roles/owner'])),
        ])

        self.assertItemsEqual(expected_violations, actual_violations)

    def test_org_project_inherit_org_rule_violation(self):
        """Test org with blacklist and child with whitelist, no inherit.

        Test that the project whitelist rule overrides the org blacklist rule
        when the project does not inherit from parent.

        Setup:
            * Create a RulesEngine with RULES5 rule set.
            * Create policy.

        Expected result:
            * Find 1 rule violation.
        """
        # actual
        rules_local_path = get_datafile_path(__file__, 'test_rules_1.yaml')
        rules_engine = OrgRulesEngine(rules_local_path)
        rules5 = copy.deepcopy(test_rules.RULES5)
        rules5['rules'][1]['inherit_from_parents'] = True
        rules_engine.rule_book = OrgRuleBook(rules5)

        project_policy = {
            'bindings': [
                {
                    'role': 'roles/owner',
                    'members': [
                        'user:owner@company.com',
                    ]
                }
            ]
        }

        actual_violations = set(
            rules_engine.find_policy_violations(self.project1, project_policy)
        )

        # expected
        expected_outstanding_proj = {
            'roles/owner': [
                IamPolicyMember.create_from('user:owner@company.com')
            ]
        }

        expected_violations = set([
            RuleViolation(
                rule_index=0,
                rule_name='org blacklist',
                resource_id=self.project1.resource_id,
                resource_type=self.project1.resource_type,
                violation_type='ADDED',
                role=project_policy['bindings'][0]['role'],
                members=tuple(expected_outstanding_proj['roles/owner'])),
        ])

        self.assertItemsEqual(expected_violations, actual_violations)

    def test_org_self_bl_proj_noinherit_wl_no_violation(self):
        """Test proj policy doesn't violate rule b/l user (org), w/l (project).

        Test that an org with a blacklist on the org level plus a project
        whitelist with no rule inheritance allows the user blacklisted by
        the org, on the project level.

        Setup:
            * Create a RulesEngine with RULES5 rule set.
            * Tweak the rules to make the org blacklist apply to "self".
            * Create policy.

        Expected result:
            * Find 0 rule violations.
        """
        # actual
        rules_local_path = get_datafile_path(__file__, 'test_rules_1.yaml')
        rules_engine = OrgRulesEngine(rules_local_path)
        rules5 = copy.deepcopy(test_rules.RULES5)
        rules5['rules'][0]['resource'][0]['applies_to'] = 'self'
        rules_engine.rule_book = OrgRuleBook(rules5)

        project_policy = {
            'bindings': [{
                    'role': 'roles/owner',
                    'members': [
                        'user:owner@company.com',
                    ]
                }]
        }

        actual_violations = set(
            rules_engine.find_policy_violations(self.project1, project_policy)
        )

        # expected
        expected_violations = set([])

        self.assertItemsEqual(expected_violations, actual_violations)

    def test_org_self_wl_proj_noinherit_bl_has_violation(self):
        """Test org allowing user + proj blacklisting user has violation.

        Test that org children whitelist with org children blacklist rules
        report violation.

        Setup:
            * Create a RulesEngine with RULES6 rule set.
            * Create policy.

        Expected result:
            * Find 1 rule violation.
        """
        # actual
        rules_local_path = get_datafile_path(__file__, 'test_rules_1.yaml')
        rules_engine = OrgRulesEngine(rules_local_path)
        rules6 = copy.deepcopy(test_rules.RULES6)
        rules6['rules'][0]['resource'][0]['applies_to'] = 'self'
        rules_engine.rule_book = OrgRuleBook(rules6)

        org_policy = {
            'bindings': [
                {
                    'role': 'roles/owner',
                    'members': [
                        'user:owner@company.com',
                    ]
                }
            ]
        }

        project_policy = {
            'bindings': [
                {
                    'role': 'roles/owner',
                    'members': [
                        'user:owner@company.com',
                    ]
                }
            ]
        }

        actual_violations = set(itertools.chain(
            rules_engine.find_policy_violations(self.org789, org_policy),
            rules_engine.find_policy_violations(self.project1, project_policy)
        ))

        # expected
        expected_outstanding_proj = {
            'roles/owner': [
                IamPolicyMember.create_from('user:owner@company.com')
            ]
        }

        expected_violations = set([
            RuleViolation(
                rule_index=1,
                rule_name='project blacklist',
                resource_id=self.project1.resource_id,
                resource_type=self.project1.resource_type,
                violation_type='ADDED',
                role=project_policy['bindings'][0]['role'],
                members=tuple(expected_outstanding_proj['roles/owner'])),
        ])

        self.assertItemsEqual(expected_violations, actual_violations)

    def test_ignore_case_works(self):
        """Test blacklisted user with different case still violates rule.

        Test that a project's user with a multi-case identifier still
        violates the blacklist.

        Setup:
            * Create a RulesEngine with RULES6 rule set.
            * Create policy.

        Expected result:
            * Find 1 rule violation.
        """
        # actual
        rules_local_path = get_datafile_path(__file__, 'test_rules_1.yaml')
        rules_engine = OrgRulesEngine(rules_local_path)
        rules_engine.rule_book = OrgRuleBook(test_rules.RULES6)

        project_policy = {
            'bindings': [
                {
                    'role': 'roles/owner',
                    'members': [
                        'user:OWNER@company.com',
                    ]
                }
            ]
        }

        actual_violations = set(itertools.chain(
            rules_engine.find_policy_violations(self.project1, project_policy)
        ))

        # expected
        expected_outstanding_proj = {
            'roles/owner': [
                IamPolicyMember.create_from('user:OWNER@company.com')
            ]
        }

        expected_violations = set([
            RuleViolation(
                rule_index=1,
                rule_name='project blacklist',
                resource_id=self.project1.resource_id,
                resource_type=self.project1.resource_type,
                violation_type='ADDED',
                role=project_policy['bindings'][0]['role'],
                members=tuple(expected_outstanding_proj['roles/owner'])),
        ])

        self.assertItemsEqual(expected_violations, actual_violations)


if __name__ == '__main__':
    basetest.main()

"""Fidelity gate accuracy matrix.

Faithful paraphrases must pass (otherwise every AI rewrite gets thrown away and
users get their original text back). Meaning changes must fail (otherwise a
rewrite can silently alter a claim). Both directions are measured here.
"""
import pytest

from humanizer.engine import check_fidelity

FAITHFUL = [
    ("Costs were reduced significantly after the new process was introduced.",
     "Once the new process came in, costs dropped sharply."),
    ("The drug did not reduce mortality in the trial (Smith, 2020).",
     "In the trial, the drug failed to lower mortality (Smith, 2020)."),
    ("Temperatures rose by 1.5 °C between 1900 and 2020 [3].",
     "Between 1900 and 2020, temperatures climbed 1.5 °C [3]."),
    ("This approach could potentially improve accuracy.", "This approach could improve accuracy."),
    ("Machine learning algorithms can analyze vast amounts of medical data, identifying patterns that would be impossible for human practitioners to detect.",
     "Machine learning can sift through huge volumes of medical data. It spots patterns no human practitioner could pick out."),
    ("Remote work has increased flexibility for employees, but it has also reduced face-to-face collaboration.",
     "Remote work gives employees more flexibility. The trade-off is less face-to-face collaboration."),
    ("The results suggest that the catalyst may lower the activation barrier.",
     "The catalyst may lower the activation barrier, the results suggest."),
    ("Furthermore, the yield increased to 82% when the temperature was raised to 80 °C.",
     "Raising the temperature to 80 °C pushed the yield up to 82%."),
    ("It is important to note that no adverse events were reported during the study.",
     "No adverse events were reported during the study."),
    ("The model was trained on 10,000 images and achieved 94.2% accuracy.",
     "Trained on 10000 images, the model reached 94.2% accuracy."),
    ("Students who used the platform appeared to engage more with the course material.",
     "Students on the platform seemed to engage more with the course material."),
    ("The policy may have contributed to lower unemployment rates in 2021.",
     "In 2021 the policy may have helped bring unemployment rates down."),
    ("The samples were heated for 2 h at 120 °C and then cooled to room temperature.",
     "We heated the samples at 120 °C for 2 h, then let them cool to room temperature."),
    ("Smith et al. (2019) found that sleep deprivation impairs memory consolidation.",
     "Sleep deprivation impairs memory consolidation, as Smith et al. (2019) found."),
    ("The difference was not statistically significant (p = 0.21).",
     "The difference did not reach statistical significance (p = 0.21)."),
    ("Prices fell sharply in March, but they recovered by June.",
     "Prices dropped hard in March. By June they had recovered."),
    ("Additionally, the survey revealed that most respondents preferred online classes.",
     "The survey also showed that most respondents preferred online classes."),
    ("These findings indicate that exercise likely improves mood in older adults.",
     "Exercise probably lifts mood in older adults, these findings indicate."),
    ("The bridge, which was completed in 1932, remains a major landmark.",
     "Completed in 1932, the bridge is still a major landmark."),
    ("Without adequate funding, the project cannot continue.",
     "The project cannot continue without adequate funding."),
    ("Renewable energy sources play a crucial role in reducing greenhouse gas emissions.",
     "Renewable energy is central to cutting greenhouse gas emissions."),
    ("The company doubled its revenue between 2018 and 2022.",
     "Between 2018 and 2022 the company doubled its revenue."),
    ("Researchers observed fewer infections in the treated group [4, 5].",
     "The treated group had fewer infections, researchers observed [4, 5]."),
    ("The algorithm reduces memory usage by roughly 30%.",
     "Memory use drops by about 30% with the algorithm."),
    ("Participants were randomly assigned to one of two groups.",
     "We randomly split participants into one of two groups."),
]

UNFAITHFUL = [
    ("The drug did not reduce mortality.", "The drug reduced mortality."),
    ("We observed no benefit.", "We observed a clear benefit."),
    ("The difference was not statistically significant.", "The difference was statistically significant."),
    ("Temperatures rose by 1.5 °C.", "Temperatures fell by 1.5 °C."),
    ("The treatment decreased mortality.", "The treatment increased mortality."),
    ("Prices fell in March.", "Prices rose in March."),
    ("The results suggest the catalyst may help.", "The results prove the catalyst helps."),
    ("The treatment may improve outcomes.", "The treatment improves outcomes."),
    ("The treatment might improve survival.", "The treatment likely improves survival."),
    ("Exercise could reduce stress.", "Exercise definitely reduces stress."),
    ("The yield was 82%.", "The yield was 85%."),
    ("The dose was 5 mg daily.", "The dose was 5 kg daily."),
    ("The result had p < 0.05.", "The result had p > 0.05."),
    ("The model achieved 94.2% accuracy.", "The model achieved 94% accuracy."),
    ("The study enrolled 120 patients.", "The study enrolled 120 patients in 2021."),
    ("Both trials had 82% yield and 82% purity.", "Both trials had 82% yield and purity."),
    ("The effect was confirmed [3].", "The effect was confirmed [4]."),
    ("This was reported by (Lee, 2021).", "This was reported by (Lee, 2022)."),
    ("The effect was confirmed [3].", "The effect was confirmed."),
    ("See https://example.org/a for details.", "See https://example.com/a for details."),
    ("The catalyst lowered the barrier.", "The dog chased a ball."),
    ("The bridge was completed in 1932.", "The bridge was completed in 1923."),
    ("She called it \"statistically significant\".", "She called it \"significant\"."),
    ("The solution was 5 μM.", "The solution was 5 mM."),
    ("Without funding, the project cannot continue.", "With funding, the project can continue."),
    # Invented claims (observed live from a real model) must be rejected.
    ("Machine learning frameworks enabled advances in medical imaging and drug discovery.",
     "Machine learning frameworks enabled advances in medical imaging and drug discovery. These tools also aid risk prediction for chronic diseases."),
    ("Remote work lets employees avoid long commutes.",
     "Remote work lets employees avoid long commutes. Regulatory frameworks governing taxation remain unsettled."),
]


@pytest.mark.parametrize("src,cand", FAITHFUL)
def test_faithful_paraphrases_pass(src, cand):
    ok, reason = check_fidelity(src, cand, short=True)
    assert ok, reason


@pytest.mark.parametrize("src,cand", UNFAITHFUL)
def test_meaning_changes_fail(src, cand):
    assert not check_fidelity(src, cand, short=True)[0]

Short description

Closes ...

#### Background

What is the context of work?

#### Goals

What is the higher-level goal of the work? Goal list

- ...

to cover.

#### Tasks

Complete

- [ ] ...

to cover all goals.

#### Tests

Good practice is to test everything you implement.

Functionality and performance testing is especially important for any contribution affecting public images. Publication process requires that there are no performance regressions and enforcing that needs continuous testing to avoid surprises during the official quality assurance process.

Remember to make changes to all Dockerfiles and CI configurations when applicable.

Depending on the scope, test for functionality and performance

- [ ] on all officially supported workloads
- [ ] on all officially supported architectures
- [ ] on whatever other part that your work touches upon 

and prefer to report your numbers in the pull-request. Moreover, make sure that the output quality is acceptable.

Use existing performance results as a reference and run CI pipeline to validate builds and the resulting image when applicable and CI resource consumption does not prevent this.

#### Other

Whatever relevant

rootProject.name = "mixed-topology"
include(":app", ":library", ":feature", ":native", ":maven-child")
project(":maven-child").projectDir = file("jvm/child")

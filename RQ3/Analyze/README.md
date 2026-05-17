## Regarding the row
repo	
model	
candidates : prediction
removed_deps : The repositories that rmoved
must_keep_deps	: The dependencies should be keep. 
baseline_result	: First test result
baseline_duration_sec
bulk_result	: After removing these removing candidate, checked the test result
post_removal_result	: After checking bulk_result (delete all deps), removed deps each by each.
n_iterations	
error


repo_precision :bulk_result PASS / (bulk_result PASS + bulk_result FAIL)		

pkg_precision  : n_safe_pkgs PASS / (n_safe_pkgs PASS + n_safe_pkgs FAIL)

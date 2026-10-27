# Official village directory assets

Checked 2026-10-27. Source: https://mahavillages.mahabhumi.gov.in/all_list.php

Coverage: Nashik district only. Verified entries: 1981. Districts: 1. Talukas: 15.

The public directory browser calls all_list_back.php with lgdState=27, lgdlocalbody=V, Roption=V1 and postAction=AjaxSearchRecord1dist. A blank district/taluka filter requests the statewide directory. This import uses the same public interface.

`villages-index.json` maps each covered taluka to a small JSON chunk. Each chunk contains code, name, nameMr, districtCode and talukaCode copied from the official response. Codes remain strings.

Records were validated against the checked district and taluka directory; village codes are unique. Coverage and any rejected rows are explicit in the manifest. This is administrative reference data, not live household or environmental observations.

No personal or household records were downloaded. Do not infer water supply, population or ownership from the administrative directory.

Nashik is complete against the checked directory snapshot: 1,981 unique village codes and all 15 parent talukas. Statewide coverage remains incomplete; the 35 other districts have no imported village index. The manifest records both states separately.

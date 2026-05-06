# Solution
Change signature to `list_users(page=1, per_page=10)`, validate inputs, slice `_USERS[(page-1)*per_page : page*per_page]`, return the dict.

import csv, collections, os

files = [
    ("/work/rintaro-k/research/RQ3/Code/python/data_dependency_waste_project/ps6_filtered/datasets/final_dataset_0.csv", "python_0"),
    ("/work/rintaro-k/research/RQ3/Code/python/data_dependency_waste_project/ps6_filtered/datasets/final_dataset_1.csv", "python_1"),
    ("/work/rintaro-k/research/RQ3/Code/csharp/data_dependency_waste_project/ps6_filtered/datasets/final_dataset_0.csv", "csharp_0"),
    ("/work/rintaro-k/research/RQ3/Code/rust/data_dependency_waste_project/ps6_filtered/datasets/final_dataset_0.csv", "rust_0"),
]

for fpath, label in files:
    print(f"\n=== {label} : {fpath} ===")
    if not os.path.exists(fpath):
        print(f"FILE NOT FOUND")
        continue
    try:
        with open(fpath, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        print(f"Total rows: {len(rows)}")

        # build_status distribution
        bs = collections.Counter(r.get('build_status','') for r in rows)
        print(f"build_status: success={bs.get('success',0)}, failure={bs.get('failure',0)}, unknown={bs.get('unknown',0)}, other={dict((k,v) for k,v in bs.items() if k not in ('success','failure','unknown'))}")

        # parent_build_status distribution
        pbs = collections.Counter(r.get('parent_build_status','') for r in rows)
        print(f"parent_build_status: success={pbs.get('success',0)}, failure={pbs.get('failure',0)}, unknown={pbs.get('unknown',0)}, other={dict((k,v) for k,v in pbs.items() if k not in ('success','failure','unknown'))}")

        # CI available rows: both != unknown
        ci_avail = sum(1 for r in rows if r.get('build_status','') != 'unknown' and r.get('parent_build_status','') != 'unknown')
        print(f"CI available rows (both != unknown): {ci_avail}")

        # Check columns
        if rows:
            cols = list(rows[0].keys())
            llama_cols = [c for c in cols if 'llama' in c]
            print(f"llama columns: {llama_cols}")

            if 'llama_success' in cols:
                lsucc = sum(1 for r in rows if r.get('llama_success','') == 'True')
                print(f"llama_success=True: {lsucc}")
            else:
                print(f"(no llama_success column)")

        # llama_dep_status distribution
        lds = collections.Counter(r.get('llama_dep_status','') for r in rows)
        print(f"llama_dep_status: used={lds.get('used',0)}, unused={lds.get('unused',0)}, unknown={lds.get('unknown',0)}, other={dict((k,v) for k,v in lds.items() if k not in ('used','unused','unknown'))}")

        # llama_is_skippable=True
        lskip = sum(1 for r in rows if r.get('llama_is_skippable','') == 'True')
        print(f"llama_is_skippable=True: {lskip}")

    except Exception as e:
        print(f"ERROR: {e}")

# Also look for dependency_data JSON files
print("\n=== Searching for dependency_data JSON files ===")
for base in [
    "/work/rintaro-k/research/RQ3/Code/python/data_dependency_waste_project/ps6_filtered/dependency_data/",
    "/work/rintaro-k/research/RQ3/Code/python/data_dependency_waste_project/ps6_filtered/batch_0/dependency_data/",
]:
    if os.path.isdir(base):
        files_in_dir = os.listdir(base)
        json_files = [f for f in files_in_dir if f.endswith('.json')]
        print(f"{base}: {len(json_files)} JSON files found")
        if json_files:
            import json
            sample = os.path.join(base, json_files[0])
            print(f"  First: {sample}")
            with open(sample) as jf:
                data = json.load(jf)
            print(f"  Keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
            if isinstance(data, dict):
                for k in ['llama_unused_dep','llama_unused_dev_dep','llama_missing_dep','llama_dep_status']:
                    if k in data:
                        print(f"  {k}: {data[k]}")
    else:
        print(f"{base}: NOT FOUND")

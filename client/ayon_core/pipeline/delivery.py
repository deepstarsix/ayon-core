"""Functions useful for delivery of published representations."""
import os
import copy
import shutil
import glob
import csv
import collections
from datetime import datetime
from typing import Dict, Any, Iterable, List

import clique
import ayon_api

from ayon_core.lib import create_hard_link

from .template_data import (
    get_general_template_data,
    get_folder_template_data,
    get_task_template_data,
)

CSV_FIELDS = [
    "Submission Date",
    "Vendor",
    "Shot name",
    "Submission status",
    "Submission filename",
    "Submission version",
    "Submission format",
    "Frame Start",
    "Frame End",
    "Handle Start",
    "Handle End",
    "Vendor Submission Note",
]

# Formats to track in delivery CSV
TRACKABLE_FORMATS = {"exr", "mov", "mp4", "dnxhd", "h264", "avi", "mxf"}

def _copy_file(src_path, dst_path):
    """Hardlink file if possible(to save space), copy if not.

    Because of using hardlinks should not be function used in other parts
    of pipeline.
    """

    if os.path.exists(dst_path):
        return
    try:
        create_hard_link(
            src_path,
            dst_path
        )
    except OSError:
        shutil.copyfile(src_path, dst_path)


def get_delivery_csv_root(delivery_path, anatomy=None):
    """
    Returns the "_delivery" directory at the project level.
    Example: /mnt/Post/Developing/shots/sh010/file.exr -> /mnt/Post/Developing/_delivery

    Args:
        delivery_path (str): Path to the delivered file
        anatomy (Anatomy, optional): Project anatomy object to get root paths

    Returns:
        str: Path to the _delivery directory at project root
    """
    abs_path = os.path.abspath(delivery_path)
    project_root = None

    # Try to get project root from anatomy first
    if anatomy:
        roots = anatomy.roots

        # Try to find which root the delivery_path belongs to
        for root_name, root_item in roots.items():
            root_path = str(root_item)
            root_path = os.path.abspath(root_path)

            if abs_path.startswith(root_path):
                # Found matching root, now get the project folder
                relative_path = abs_path[len(root_path):].lstrip(os.sep)

                # Get the first directory component (the project name)
                path_parts = relative_path.split(os.sep)
                if path_parts and path_parts[0]:
                    project_folder = path_parts[0]
                    project_root = os.path.join(root_path, project_folder)
                else:
                    # Fallback to just the root if no project folder found
                    project_root = root_path
                break

        # Fallback to first available root if no match found
        if not project_root and roots:
            first_root = str(list(roots.values())[0])
            project_root = os.path.abspath(first_root)

    # Fallback to path parsing if anatomy not available or didn't work
    if not project_root:
        parts = abs_path.split(os.sep)

        if os.name == "nt":
            # Windows: C:\path\to\project\file
            parts = [p for p in parts if p]
            if len(parts) >= 3:
                project_root = os.path.join(parts[0] + os.sep, parts[1], parts[2])
            elif len(parts) >= 2:
                project_root = os.path.join(parts[0] + os.sep, parts[1])
            elif len(parts) == 1:
                project_root = parts[0] + os.sep
            else:
                project_root = os.getcwd()
        else:
            # Linux/Mac: /mnt/Post/Developing/...
            meaningful_parts = [p for p in parts if p]

            if len(meaningful_parts) >= 3:
                project_root = os.sep + os.path.join(
                    meaningful_parts[0],
                    meaningful_parts[1],
                    meaningful_parts[2]
                )
            elif len(meaningful_parts) >= 2:
                project_root = os.sep + os.path.join(
                    meaningful_parts[0],
                    meaningful_parts[1]
                )
            elif len(meaningful_parts) == 1:
                project_root = os.sep + meaningful_parts[0]
            else:
                project_root = os.sep

    delivery_root = os.path.join(project_root, "_delivery")
    return delivery_root


def append_exr_to_global_csv(csv_path, row_data):
    """Append delivery information to global CSV, avoiding duplicates."""
    # Avoid duplicates: read existing CSV and only write if not present
    existing_rows = set()
    if os.path.exists(csv_path):
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = tuple(row[field] for field in CSV_FIELDS)
                existing_rows.add(key)

    key = tuple(str(row_data.get(field, "")) for field in CSV_FIELDS)

    if key in existing_rows:
        return  # Don't write duplicate row

    write_header = not os.path.exists(csv_path)

    with open(csv_path, mode="a", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow(row_data)


def write_debug_log(debug_info, csv_path):
    """
    Writes debug log to a .txt file next to the delivery_log.csv.
    """
    log_path = os.path.splitext(csv_path)[0] + "_debug.txt"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {debug_info}\n")


def extract_frame_from_filename(filename: str) -> str:
    """Extract the frame number from filename, expects frame just before extension, separated by dot."""
    base = os.path.basename(filename)
    parts = base.split('.')
    numeric_parts = [part for part in parts[:-1] if part.isdigit()]
    if numeric_parts:
        return numeric_parts[-1]
    return ""


def sort_files_by_frame(files):
    """Sort files by frame number."""
    def get_frame_int(fname):
        frame = extract_frame_from_filename(fname)
        try:
            return int(frame)
        except Exception:
            return -1
    return sorted(files, key=get_frame_int)


def create_submission_row(
    context: dict,
    version: str,
    file_format: str,
    sequence_files: List[str],
    description: str = "",
    frame_start: str = "",
    frame_end: str = "",
    handle_start: str = "",
    handle_end: str = "",
    debug_log: List[str] = None
) -> dict:
    """
    Create a CSV row for delivery submission tracking.

    Args:
        context: Representation context dictionary
        version: Version number
        file_format: File format/extension (e.g., 'exr', 'mov', 'h264')
        sequence_files: List of file paths in the sequence
        description: Version description/comment
        frame_start: Frame start number
        frame_end: Frame end number
        handle_start: Handle start number
        handle_end: Handle end number
        debug_log: Optional debug log list

    Returns:
        Dictionary with CSV row data
    """
    version_padded = f"v{int(version):03}"
    folder = context.get("folder", {}).get("name", "")

    shot_name = f"{folder}"
    submission_status = "for review"
    submission_filename = f"{folder}_comp_POSTER_{version_padded}"
    submission_version = version_padded
    submission_format = file_format.upper()  # MOV, EXR, H264, etc.
    vendor_note = description

    today = datetime.now().strftime("%Y-%m-%d")
    vendor = "Poster"

    return {
        "Submission Date": today,
        "Vendor": vendor,
        "Shot name": shot_name,
        "Submission status": submission_status,
        "Submission filename": submission_filename,
        "Submission version": submission_version,
        "Submission format": submission_format,
        "Frame Start": frame_start,
        "Frame End": frame_end,
        "Handle Start": handle_start,
        "Handle End": handle_end,
        "Vendor Submission Note": vendor_note,
    }


def get_format_dict(anatomy, location_path):
    """Returns replaced root values from user provider value.

    Args:
        anatomy (Anatomy): Project anatomy.
        location_path (str): User provided value.

    Returns:
        (dict): Prepared data for formatting of a template.
    """

    format_dict = {}
    if not location_path:
        return format_dict

    location_path = location_path.replace("\\", "/")
    root_names = anatomy.root_names_from_templates(
        anatomy.templates["delivery"]
    )
    format_dict["root"] = {}
    for name in root_names:
        format_dict["root"][name] = location_path
    return format_dict


def check_destination_path(
    repre_id,
    anatomy,
    anatomy_data,
    datetime_data,
    template_name
):
    """ Try to create destination path based on 'template_name'.

    In the case that path cannot be filled, template contains unmatched
    keys, provide error message to filter out repre later.

    Args:
        repre_id (str): Representation id.
        anatomy (Anatomy): Project anatomy.
        anatomy_data (dict): Template data to fill anatomy templates.
        datetime_data (dict): Values with actual date.
        template_name (str): Name of template which should be used from anatomy
            templates.
    Returns:
        Dict[str, List[str]]: Report of happened errors. Key is message title
            value is detailed information.
    """

    anatomy_data.update(datetime_data)
    path_template = anatomy.get_template_item(
        "delivery", template_name, "path"
    )
    dest_path = path_template.format(anatomy_data)
    report_items = collections.defaultdict(list)

    if not dest_path.solved:
        msg = (
            "Missing keys in Representation's context"
            " for anatomy template \"{}\"."
        ).format(template_name)

        sub_msg = (
            "Representation: {}<br>"
        ).format(repre_id)

        if dest_path.missing_keys:
            keys = ", ".join(dest_path.missing_keys)
            sub_msg += (
                "- Missing keys: \"{}\"<br>"
            ).format(keys)

        if dest_path.invalid_types:
            items = []
            for key, value in dest_path.invalid_types.items():
                items.append("\"{}\" {}".format(key, str(value)))

            keys = ", ".join(items)
            sub_msg += (
                "- Invalid value DataType: \"{}\"<br>"
            ).format(keys)

        report_items[msg].append(sub_msg)

    return report_items


def deliver_single_file(
    src_path,
    repre,
    anatomy,
    template_name,
    anatomy_data,
    format_dict,
    report_items,
    log
):
    """Copy single file to calculated path based on template

    Args:
        src_path(str): path of source representation file
        repre (dict): full repre, used only in deliver_sequence, here only
            as to share same signature
        anatomy (Anatomy)
        template_name (string): user selected delivery template name
        anatomy_data (dict): data from repre to fill anatomy with
        format_dict (dict): root dictionary with names and values
        report_items (collections.defaultdict): to return error messages
        log (logging.Logger): for log printing

    Returns:
        (collections.defaultdict, int)
    """

    # Make sure path is valid for all platforms
    src_path = os.path.normpath(src_path.replace("\\", "/"))

    if not os.path.exists(src_path):
        msg = "{} doesn't exist for {}".format(src_path, repre["id"])
        report_items["Source file was not found"].append(msg)
        return report_items, 0

    if format_dict:
        anatomy_data = copy.deepcopy(anatomy_data)
        anatomy_data["root"] = format_dict["root"]
    template_obj = anatomy.get_template_item(
        "delivery", template_name, "path"
    )
    delivery_path = template_obj.format_strict(anatomy_data)

    # Backwards compatibility when extension contained `.`
    delivery_path = delivery_path.replace("..", ".")
    # Make sure path is valid for all platforms
    delivery_path = os.path.normpath(delivery_path.replace("\\", "/"))
    # Remove newlines from the end of the string to avoid OSError during copy
    delivery_path = delivery_path.rstrip()

    delivery_folder = os.path.dirname(delivery_path)
    if not os.path.exists(delivery_folder):
        os.makedirs(delivery_folder)

    log.debug("Copying single: {} -> {}".format(src_path, delivery_path))
    _copy_file(src_path, delivery_path)

    # Delivery CSV logging for trackable formats
    context = repre["context"]
    ext = context.get("ext", "").lower()

    if ext in TRACKABLE_FORMATS:
        version = context.get("version", "")

        # Try to get description and frame data from version entity
        description = ""
        frame_start = ""
        frame_end = ""
        handle_start = ""
        handle_end = ""

        project_name = context.get("project", {}).get("name")
        version_id = repre.get("versionId")

        if project_name and version_id:
            try:
                version_entity = ayon_api.get_version_by_id(project_name, version_id)

                if version_entity:
                    # Get description/comment
                    description = version_entity.get("attrib", {}).get("description", "")
                    if not description:
                        description = version_entity.get("attrib", {}).get("comment", "")
                    if not description:
                        description = version_entity.get("data", {}).get("comment", "")

                    # Get frame data from attrib
                    attrib = version_entity.get("attrib", {})
                    frame_start = str(attrib.get("frameStart", ""))
                    frame_end = str(attrib.get("frameEnd", ""))

                    # Get handle data from attrib
                    handle_start = str(attrib.get("handleStart", ""))
                    handle_end = str(attrib.get("handleEnd", ""))

                    # Alternative: try data section if not in attrib
                    if not frame_start:
                        data = version_entity.get("data", {})
                        frame_start = str(data.get("frameStart", ""))
                        frame_end = str(data.get("frameEnd", ""))
                        handle_start = str(data.get("handleStart", ""))
                        handle_end = str(data.get("handleEnd", ""))

            except Exception as e:
                log.warning(f"Could not fetch version data: {e}")

        row = create_submission_row(
            context,
            version,
            ext,
            [src_path],
            description=description,
            frame_start=frame_start,
            frame_end=frame_end,
            handle_start=handle_start,
            handle_end=handle_end
        )

        delivery_root = get_delivery_csv_root(delivery_path, anatomy)

        if not os.path.exists(delivery_root):
            os.makedirs(delivery_root, exist_ok=True)

        csv_path = os.path.join(delivery_root, "delivery_log.csv")

        append_exr_to_global_csv(csv_path, row)
        write_debug_log('', csv_path)

    return report_items, 1


def deliver_sequence(
    src_path,
    repre,
    anatomy,
    template_name,
    anatomy_data,
    format_dict,
    report_items,
    log,
    has_renumbered_frame=False,
    new_frame_start=0
):
    """ For Pype2(mainly - works in 3 too) where representation might not
        contain files.

        Uses listing physical files (not 'files' on repre as a)might not be
         present, b)might not be reliable for representation and copying them.

         TODO Should be refactored when files are sufficient to drive all
         representations.

    Args:
        src_path(str): path of source representation file
        repre (dict): full representation
        anatomy (Anatomy)
        template_name (string): user selected delivery template name
        anatomy_data (dict): data from repre to fill anatomy with
        format_dict (dict): root dictionary with names and values
        report_items (collections.defaultdict): to return error messages
        log (logging.Logger): for log printing

    Returns:
        (collections.defaultdict, int)
    """

    src_path = os.path.normpath(src_path.replace("\\", "/"))

    def hash_path_exist(myPath):
        res = myPath.replace('#', '*')
        glob_search_results = glob.glob(res)
        if len(glob_search_results) > 0:
            return True
        return False

    if not hash_path_exist(src_path):
        msg = "{} doesn't exist for {}".format(
            src_path, repre["id"])
        report_items["Source file was not found"].append(msg)
        return report_items, 0

    delivery_template = anatomy.get_template_item(
        "delivery", template_name, "path", default=None
    )
    if delivery_template is None:
        msg = (
            "Delivery template \"{}\" in anatomy of project \"{}\""
            " was not found"
        ).format(template_name, anatomy.project_name)
        report_items[""].append(msg)
        return report_items, 0

    # Check if 'frame' key is available in template which is required
    #   for sequence delivery
    if "{frame" not in delivery_template.template:
        msg = (
            "Delivery template \"{}\" in anatomy of project \"{}\""
            "does not contain '{{frame}}' key to fill. Delivery of sequence"
            " can't be processed."
        ).format(template_name, anatomy.project_name)
        report_items[""].append(msg)
        return report_items, 0

    dir_path, _file_name = os.path.split(str(src_path))

    context = repre["context"]
    ext = context.get("ext", context.get("representation"))

    if not ext:
        msg = "Source extension not found, cannot find collection"
        report_items[msg].append(src_path)
        log.warning("{} <{}>".format(msg, context))
        return report_items, 0

    ext = "." + ext
    # context.representation could be .psd
    ext = ext.replace("..", ".")

    src_collections, _remainder = clique.assemble(os.listdir(dir_path))
    src_collection = None
    for col in src_collections:
        if col.tail != ext:
            continue

        src_collection = col
        break

    if src_collection is None:
        msg = "Source collection of files was not found"
        report_items[msg].append(src_path)
        log.warning("{} <{}>".format(msg, src_path))
        return report_items, 0

    frame_indicator = "@####@"

    anatomy_data = copy.deepcopy(anatomy_data)
    anatomy_data["frame"] = frame_indicator
    if format_dict:
        anatomy_data["root"] = format_dict["root"]
    delivery_path = delivery_template.format_strict(anatomy_data)

    delivery_path = os.path.normpath(delivery_path.replace("\\", "/"))
    delivery_folder = os.path.dirname(delivery_path)
    dst_head, dst_tail = delivery_path.split(frame_indicator)
    dst_padding = src_collection.padding
    dst_collection = clique.Collection(
        head=dst_head,
        tail=dst_tail,
        padding=dst_padding
    )

    if not os.path.exists(delivery_folder):
        os.makedirs(delivery_folder)

    src_head = src_collection.head
    src_tail = src_collection.tail
    uploaded = 0
    first_frame = min(src_collection.indexes)
    seq_files_set = set()

    for index in src_collection.indexes:
        src_padding = src_collection.format("{padding}") % index
        src_file_name = "{}{}{}".format(src_head, src_padding, src_tail)
        src = os.path.normpath(
            os.path.join(dir_path, src_file_name)
        )
        seq_files_set.add(src)
        dst_index = index
        if has_renumbered_frame:
            # Calculate offset between first frame and current frame
            # - '0' for first frame
            offset = new_frame_start - first_frame
            # Add offset to new frame start
            dst_index = index + offset
            if dst_index < 0:
                msg = "Renumber frame has a smaller number than original frame"     # noqa
                report_items[msg].append(src_file_name)
                log.warning("{} <{}>".format(msg, context))
                return report_items, 0
        dst_padding = dst_collection.format("{padding}") % dst_index
        dst = "{}{}{}".format(dst_head, dst_padding, dst_tail)
        log.debug("Copying single: {} -> {}".format(src, dst))
        _copy_file(src, dst)

        uploaded += 1

    # Delivery CSV logging for trackable formats (once per sequence, not per frame)
    seq_files = list(seq_files_set)
    ext_clean = context.get("ext", "").lower()

    if ext_clean in TRACKABLE_FORMATS and seq_files:
        version = context.get("version", "")

        # Try to get description and frame data from version entity
        description = ""
        frame_start = ""
        frame_end = ""
        handle_start = ""
        handle_end = ""

        project_name = context.get("project", {}).get("name")
        version_id = repre.get("versionId")

        if project_name and version_id:
            try:
                version_entity = ayon_api.get_version_by_id(project_name, version_id)

                if version_entity:
                    # Get description/comment
                    description = version_entity.get("attrib", {}).get("description", "")
                    if not description:
                        description = version_entity.get("attrib", {}).get("comment", "")
                    if not description:
                        description = version_entity.get("data", {}).get("comment", "")

                    # Get frame data from attrib
                    attrib = version_entity.get("attrib", {})
                    frame_start = str(attrib.get("frameStart", ""))
                    frame_end = str(attrib.get("frameEnd", ""))

                    # Get handle data from attrib
                    handle_start = str(attrib.get("handleStart", ""))
                    handle_end = str(attrib.get("handleEnd", ""))

                    # Alternative: try data section if not in attrib
                    if not frame_start:
                        data = version_entity.get("data", {})
                        frame_start = str(data.get("frameStart", ""))
                        frame_end = str(data.get("frameEnd", ""))
                        handle_start = str(data.get("handleStart", ""))
                        handle_end = str(data.get("handleEnd", ""))

            except Exception as e:
                log.warning(f"Could not fetch version data: {e}")

        row = create_submission_row(
            context,
            version,
            ext_clean,
            seq_files,
            description=description,
            frame_start=frame_start,
            frame_end=frame_end,
            handle_start=handle_start,
            handle_end=handle_end
        )

        delivery_root = get_delivery_csv_root(delivery_path, anatomy)

        if not os.path.exists(delivery_root):
            os.makedirs(delivery_root, exist_ok=True)

        csv_path = os.path.join(delivery_root, "delivery_log.csv")

        append_exr_to_global_csv(csv_path, row)
        write_debug_log('', csv_path)

    return report_items, uploaded


def _merge_data(data, new_data):
    queue = collections.deque()
    queue.append((data, new_data))
    while queue:
        q_data, q_new_data = queue.popleft()
        for key, value in q_new_data.items():
            if key in q_data and isinstance(value, dict):
                queue.append((q_data[key], value))
                continue
            q_data[key] = value


def get_representations_delivery_template_data(
    project_name: str,
    representation_ids: Iterable[str],
) -> Dict[str, Dict[str, Any]]:
    representation_ids = set(representation_ids)

    output = {
        repre_id: {}
        for repre_id in representation_ids
    }
    if not representation_ids:
        return output

    project_entity = ayon_api.get_project(project_name)

    general_template_data = get_general_template_data()

    repres_hierarchy = ayon_api.get_representations_hierarchy(
        project_name,
        representation_ids,
        project_fields=set(),
        folder_fields={"path", "folderType"},
        task_fields={"name", "taskType"},
        product_fields={"name", "productType"},
        version_fields={"version", "productId"},
        representation_fields=None,
    )
    for repre_id, repre_hierarchy in repres_hierarchy.items():
        repre_entity = repre_hierarchy.representation
        if repre_entity is None:
            continue

        template_data = repre_entity["context"]
        # Bug in 'ayon_api', 'get_representations_hierarchy' did not fully
        #   convert representation entity. Fixed in 'ayon_api' 1.0.10.
        if isinstance(template_data, str):
            con = ayon_api.get_server_api_connection()
            con._representation_conversion(repre_entity)
            template_data = repre_entity["context"]

        template_data.update(copy.deepcopy(general_template_data))
        template_data.update(get_folder_template_data(
            repre_hierarchy.folder, project_name
        ))
        if repre_hierarchy.task:
            template_data.update(get_task_template_data(
                project_entity, repre_hierarchy.task
            ))

        product_entity = repre_hierarchy.product
        version_entity = repre_hierarchy.version
        template_data.update({
            "product": {
                "name": product_entity["name"],
                "type": product_entity["productType"],
            },
            "version": version_entity["version"],
        })
        _merge_data(template_data, repre_entity["context"])

        # Remove roots from template data to auto-fill them with anatomy data
        template_data.pop("root", None)

        output[repre_id] = template_data
    return output

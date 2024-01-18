# dependencies: numpy, pydicom, pil, customtkinter

import tkinter as tk
import os
from tkinter import ttk, messagebox, simpledialog
import pydicom
from PIL import Image, ImageTk
from tkinter import filedialog
import customtkinter as ctk

# Declare global DICOM dataset
ds = None
metadata_text = None  # Added to declare metadata_text as a global variable


def load_dicom_image():
  global tk_image
  global canvas
  global canvas_image
  global loaded_image
  global ds
  global current_file_label
  global export_button  # Hinzugefügt, um auf den Export-JPG-Button zugreifen zu können

  # Show dialog to open a DICOM file
  file_path = filedialog.askopenfilename(
      filetypes=[("DICOM Files", "*.dcm"), ("All files", "*.*")])

  if file_path:
    try:
      # Open DICOM file and retrieve metadata
      ds = pydicom.dcmread(file_path)
      patient_name.set(ds.PatientName)
      study_date.set(ds.StudyDate)
      image = ds.pixel_array

      # Convert DICOM image to a PIL image and map the value range to 0-255
      image_min = image.min()
      image_max = image.max()
      image = (image - image_min) / (image_max - image_min) * 255
      loaded_image = Image.fromarray(image.astype('uint8'))

      # Adjust image display size based on the slider value
      scale_factor = scale_var.get() / 100.0
      scaled_image = loaded_image.resize(
          (int(loaded_image.width * scale_factor),
           int(loaded_image.height * scale_factor)))

      # Convert PIL image to a Tkinter image
      tk_image = ImageTk.PhotoImage(scaled_image)

      # Display image in the GUI
      canvas.delete("all")  # Clear previous image
      canvas_image = canvas.create_image(0, 0, anchor="nw", image=tk_image)
      canvas.configure(scrollregion=canvas.bbox("all"))

      # Get only the filename without the full path
      file_name = os.path.basename(file_path)

      # Update the current file label with the filename only
      current_file_label.configure(text=f"Current File: {file_name}")

    except Exception as e:
      messagebox.showerror("Error", f"Error loading the DICOM file: {str(e)}")


# Function to save the DICOM file as JPG
def export_as_jpg():
  global loaded_image

  if loaded_image:
    try:
      # Zeige einen Dialog zum Speichern der JPG-Datei an
      file_path = filedialog.asksaveasfilename(defaultextension=".jpg",
                                               filetypes=[("JPEG Files",
                                                           "*.jpg")])

      if file_path:
        # Speichern Sie das PIL-Bild als JPG-Datei
        loaded_image.save(file_path)
        messagebox.showinfo(
            "Success", f"The image was saved under {file_path} successfully.")
    except Exception as e:
      messagebox.showerror("Failure", f"Failed to save the image: {str(e)}")


# Function to change the image display size
def change_image_size(*args):
  global tk_image
  global canvas
  global canvas_image
  global loaded_image

  if loaded_image:
    # Change image display size based on the slider value
    scale_factor = scale_var.get() / 100.0
    scaled_image = loaded_image.resize(
        (int(loaded_image.width * scale_factor),
         int(loaded_image.height * scale_factor)))

    # Convert PIL image to a Tkinter image
    tk_image = ImageTk.PhotoImage(scaled_image)

    # Replace image in the canvas and adjust display area
    canvas.itemconfigure(canvas_image, image=tk_image)
    canvas.configure(scrollregion=canvas.bbox("all"))


# Function to open the metadata editor
def open_metadata():
  global ds
  global metadata_text

  if ds:
    metadata_editor = tk.Toplevel(root)
    metadata_editor.title("View Metadata")

    # Create metadata text field
    metadata_text = tk.Text(metadata_editor, wrap=tk.WORD)
    metadata_text.pack(fill=tk.BOTH, expand=True)

    # Display metadata in the text field
    metadata_text.insert(tk.END, str(ds))

    # Add search field
    search_entry = ctk.CTkEntry(metadata_editor)
    search_entry.pack(fill=ctk.BOTH, padx=40)
    search_entry.bind("<Return>", search_metadata)

    # Add cancel button
    cancel_button = ctk.CTkButton(metadata_editor,
                                  text="Cancel",
                                  command=metadata_editor.destroy)
    cancel_button.pack(side=ctk.RIGHT)


# Function to search and highlight metadata
def search_metadata(event):
  global metadata_text

  metadata_text.tag_remove("highlight", "1.0",
                           tk.END)  # Remove all previous highlights

  search_term = event.widget.get().strip(
  )  # Remove leading and trailing spaces

  if search_term:
    start_idx = "1.0"
    while True:
      start_idx = metadata_text.search(search_term,
                                       start_idx,
                                       stopindex=tk.END)
      if not start_idx:
        break
      end_idx = f"{start_idx}+{len(search_term)}c"
      metadata_text.tag_add("highlight", start_idx, end_idx)
      start_idx = end_idx
    metadata_text.tag_configure("highlight", background="yellow")


# Function to move the image with the mouse
def on_canvas_click(event):
  global canvas
  global canvas_x, canvas_y
  canvas_x, canvas_y = event.x, event.y


def on_canvas_drag(event):
  global canvas
  global canvas_x, canvas_y
  new_x, new_y = event.x, event.y

  # Calculate the displacement based on the current and previous mouse position
  dx = new_x - canvas_x
  dy = new_y - canvas_y

  # Update the image position in the canvas
  canvas.move(canvas_image, dx, dy)

  # Update the previous mouse position
  canvas_x, canvas_y = new_x, new_y


# Create the GUI with customtkinter
root = ctk.CTk()
root.title("DICOM Viewer")
root._set_appearance_mode("dark")

# Frame for the header section
header_frame = ctk.CTkFrame(root)
header_frame.grid(row=0, column=0, columnspan=4, sticky="nsew")
header_frame._set_appearance_mode("dark")

# Widgets to display patient information in the header
patient_name = ctk.StringVar()
study_date = ctk.StringVar()

name = ctk.CTkLabel(header_frame, text="Patient Name: ")
name.grid(row=0, column=0, sticky="w")
name._set_appearance_mode("dark")
nameField = ctk.CTkLabel(header_frame, textvariable=patient_name)
nameField.grid(row=0, column=1, sticky="w")
nameField._set_appearance_mode("dark")
studydate = ctk.CTkLabel(header_frame, text="Study Date:")
studydate.grid(row=1, column=0, sticky="w")
studydate._set_appearance_mode("dark")
studydateField = ctk.CTkLabel(header_frame, textvariable=study_date)
studydateField.grid(row=1, column=1, sticky="w")
studydateField._set_appearance_mode("dark")

# Button to load the DICOM file
load_button = ctk.CTkButton(header_frame,
                            text="Load DICOM File",
                            command=load_dicom_image)
load_button.grid(row=1, column=2, padx=10)
load_button._set_appearance_mode("dark")

# Button to save the DICOM file as JPG
export_button = ctk.CTkButton(header_frame,
                              text="Export as JPG",
                              command=export_as_jpg)
export_button.grid(row=1, column=4, padx=10)
export_button._set_appearance_mode("dark")

# Button to display metadata
edit_metadata_button = ctk.CTkButton(header_frame,
                                     text="Display Metadata",
                                     command=open_metadata)
edit_metadata_button.grid(row=1, column=3, padx=10)
edit_metadata_button._set_appearance_mode("dark")

# Label for Image Size Slider
imageSize = ctk.CTkLabel(header_frame, text="Image Size (%):")
imageSize.grid(row=2, column=4, sticky="w")
imageSize._set_appearance_mode("dark")

# Image display size slider
scale_var = tk.DoubleVar()
scale_var.set(100)  # Default value at 100%
scale = ctk.CTkSlider(header_frame,
                      from_=10,
                      to=200,
                      variable=scale_var,
                      command=change_image_size)
scale.grid(row=2, column=4, padx=150)
scale._set_appearance_mode("dark")

# Label to display the current file name
current_file_label = ctk.CTkLabel(header_frame, text="Current File: None")
current_file_label.grid(row=2,
                        column=0,
                        columnspan=5,
                        padx=10,
                        pady=5,
                        sticky="w")
current_file_label._set_appearance_mode("dark")

# configureure the canvas to occupy the entire window size
canvas = ctk.CTkCanvas(root)
canvas.grid(row=1, column=0, columnspan=4, sticky="nsew")

# Initialize global variables
tk_image = None
loaded_image = None
canvas_image = None
canvas_x, canvas_y = 0, 0

# Bind mouse interactions to the canvas
canvas.bind("<Button-1>", on_canvas_click)
canvas.bind("<B1-Motion>", on_canvas_drag)

# configureure grid weights to make the canvas expand with the window
root.grid_rowconfigure(1, weight=1)
root.columnconfigure(0, weight=1)

# Main loop
root.mainloop()

# Batch Image Studio

A Mac window for generating many images through the OpenAI API. Each image is a separate request. Later images are not shown to the model, so the brief does not drift the way it does in a ChatGPT session.

## Open it

Double-click `Batch Image Studio` on the Desktop. A Terminal window opens, and then the program window opens. That Terminal window stays open while the program is running. Quitting the program closes it. Closing the Terminal window does not quit the program. The Finder and Dock icon is the blue image with the product cards.

The program files stay in the `batch-img-creation` folder, which is also on the Desktop. Leave that folder where it is. The Desktop app starts the program from there.

If macOS says the app cannot be opened because the developer cannot be verified, Control-click the app, choose Open, and confirm.

`Run Batch Studio.command` opens the same program. The first launch creates a virtual environment and installs the Python packages. That step needs a network connection. Python 3.10 or newer is required. If macOS does not have it, install Python from [python.org](https://www.python.org/downloads/) and run the command again.

You can also start it from Terminal, in this folder:

```bash
.venv/bin/python -m studio
```

That Terminal window is yours. Quitting the program does not close it. Dark theme, beside the title, switches the window colors. The choice is saved with the other settings.

## API key

The program does not contain a key.

Paste the key into the field and click Save key, or put this line in `.env` in this folder. When `.env` contains a key, that file is used. The environment variable is used only when the file has no key.

```
OPENAI_API_KEY=
```

The file is kept private to your user account. ChatGPT Plus does not pay for API use. OpenAI bills the API separately. GPT Image models may also require organization verification in the OpenAI developer console.

## Create images

1. Write the prompt. Put every requirement the image must follow in that box.
2. Drop reference images. Use this for one approved example of the lighting, framing, and background.
3. Choose the output folder.
4. Choose the model. GPT Image 2.5 Sunburst is the default because it is the precise editing model. Flare is faster. The dated snapshot entries stay on one model version if you do not want the alias to move.
5. Click Generate. Command+Return does the same thing.

Leave the product list empty when every image should use the same prompt and the same reference images.

Add a product when each item has its own photographs. Import folder uses each subfolder as a product. If the folder has no subfolders, each image becomes a product.

Clear prompt, Clear references, and Clear images remove those items from the window. They do not delete the files on disk.

## Why later images drift, and what this program does instead

In ChatGPT, the next image is part of the same conversation. The app rewrites the prompt and can edit the previous picture. Each turn conditions on what came before, so the result walks away from the first brief.

This program uses the Image API, not a conversation. Every candidate is sent alone, with the original prompt and the original reference images. A rejected image is not attached to the retry. The retry repeats the original brief and the corrections collected so far. A change you type edits only the image you selected, together with the original brief. It does not accumulate a session of earlier pictures. Edits request high fidelity to the supplied images. GPT Image 2 ignores that setting. If a model rejects the setting, the same request is sent again without it.

Independent requests do not make the images identical. The model still samples, so the candidates differ. The brief stays fixed.

## Review and retries

Turn on review to send each finished image to a vision model with your criteria. The default reviewer is GPT-5.4. GPT-5.4 mini costs less and is less careful.

The reviewer can miss a bad label, a wrong color, or a detail you dislike. It can also reject an image you would have kept. The Review tab is the decision. Approve copies the file into `approved` inside the output folder. Reject leaves the file where it is.

A rejection can request another image, up to the retry limit. The new request repeats the original brief and every correction so far. It does not include the rejected picture. Retries stop when the limit or the spend cap is reached. If you approve or reject an image while the reviewer is still working, that decision stands and no automatic retry is sent. Use my scene, paste product does not request another image after a rejection, because the next paste would be the same picture.

Generate another from the original brief asks for a new image from the saved brief and references. Apply a change to this image edits the selected file and keeps the original brief in the request.

## Keep the real product pixels

Reference only lets the model create the whole image. It can redraw the package, the label, and the shape.

Lock product pixels is for a product that should stay in its original frame. Create a cutout, or choose a mask, first. The model is asked to rerender the surrounding scene. Afterward the program pastes the original product pixels back. A mask from the API is only guidance. The paste is the guarantee.

The photograph may be resized to a size the API accepts. The pixels that are pasted are that resized photograph, not a new drawing of the product. Lock mode does not use the size menu. It keeps the photograph's frame. It cannot move or recolor the product. Use Reference only if the product itself must change.

New scene, paste product asks the model for a scene and tells it not to draw the product. The product photographs are not sent to the image model. The program then pastes your cutout on the lower center. The reviewer still receives the cutout and the product photographs, so it can compare the finished picture with the real product. This requires a PNG that already has transparency, or a cutout you create in the product card. The scale is the product height as a fraction of the frame. A later change edits the scene and pastes the same cutout again. It does not redraw the product. A correction cannot move that pasted product. Change the height scale and generate again when the product sits in the wrong place.

Use my scene, paste product is for a scene you already have. Drop that scene into Reference images. The first image is kept and is not redrawn. Create a cutout on the product card. Height is the product height as a fraction of that scene. 0.62 means 62 percent of the scene height. The product is centered and sits near the bottom. If it would be wider than about 86 percent of the frame, it is shrunk. The image model is not called, and one picture is saved per product. A text correction cannot move the product. Change Height and generate again. If review is off, this mode does not need an API key. If review is on, the reviewer still calls the API.

Cut out plain background is for a product on a plain backdrop. It only changes transparency. Check the checkerboard preview. Cut out complex background uses the optional local `rembg` model and does not send the photograph to OpenAI. Install it with:

```bash
.venv/bin/python -m pip install rembg
```

The first run of that cutout can take several minutes while the model downloads.

A mask PNG uses this rule: transparent areas are regenerated and opaque areas are kept. If the file has no transparency, white is kept and black is regenerated.

## Spend

Before a run, the window shows an allowance, not a quote. The meter is filled from token counts returned by the API.

Image calls are counted at the published GPT Image 2.5 rates: $5 per million text input tokens, $8 per million image input tokens, and $30 per million image output tokens. Review calls use the published GPT-5.4 rates: $2.50 per million input tokens, $0.25 per million cached input tokens, and $15 per million output tokens. GPT-5.4 mini is counted at $0.75 and $4.50 per million, without a cached-input discount. If OpenAI changes prices, or an older image model is billed differently, the meter can disagree with the invoice. The invoice is the authority.

The cap stops new requests. One request that is already in flight can finish after the cap is reached. Stop cancels work that has not been sent. A request that has already reached OpenAI still finishes and is saved.

High, xhigh, and max quality, large sizes, reference images, review, and retries are what make a batch expensive. Start with a small candidate count. Use my scene, paste product does not send an image request. Review of that picture is still billed when review is on.

## Files

Generated images go in the output folder, under a folder named for the product. `approved` holds the copies you accept. `manifest.json` is the review list. `_workspace` holds the reference copies used for a later change. You can delete `_workspace` after you no longer need changes or retries for that run. `log.txt` is the activity log.

Settings, other than the API key, are stored in `~/Library/Application Support/BatchImageStudio`. That includes the dark theme choice.

## Limits

The API allows 16 input images on one request. The program keeps the first 16.

Account rate limits still apply. The default is 2 requests at a time. If OpenAI is busy, the same request is sent again after a wait. That wait is not a new creative retry.

Very large photographs are reduced before they are sent as references. The locked product frame is resized only when the API's size rules require it.
